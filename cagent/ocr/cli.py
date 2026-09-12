"""OCR 入口（单家 + 批量）：`uv run python cagent/ocr/cli.py ...`。

把 lakehouse raw 层 PDF 解析为 derived 层 Markdown。vLLM 服务自管：probe 到在跑的
直接复用，没有的按 lib 服务层同款命令拉起、跑完即关；全部无待解析时直接退出，
不启动服务（幂等重跑是常态）。

    uv run python cagent/ocr/cli.py --stock 09863 --market hk [--latest] [--pdf-limit N] [--force]
    uv run python cagent/ocr/cli.py --market all [--offset N] [--limit N] [--force]
"""

import argparse
import logging
import sys
from pathlib import Path

from cagent.lib.service import VLLM_CMD, VLLM_HEALTH_URL, ensure_service
from cagent.ocr import (
    LAKEHOUSE_ROOT,
    MARKETS,
    latest_pdf,
    ocr_market,
    ocr_stock,
    pending_pdfs,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stderr,
)
logging.getLogger("urllib3").setLevel(logging.WARNING)
log = logging.getLogger("ocr")

parser = argparse.ArgumentParser(
    description="把 lakehouse raw 层 PDF 解析为 derived 层 Markdown（单家公司或全市场批量）")
parser.add_argument("--stock", default="", help="股票代码，如 01748；省略则遍历市场下全部公司")
parser.add_argument("--market", choices=["all", *MARKETS], default="all",
                    help="目标市场：cn=A股, hk=港股, all=两者（默认）")
parser.add_argument("--lakehouse", default=str(LAKEHOUSE_ROOT),
                    help="lakehouse 根目录（默认 <仓库根>/.cagent）")
parser.add_argument("--offset", type=int, default=0, help="从第 N 家公司开始（0 起），用于分片")
parser.add_argument("--limit", type=int, default=0,
                    help="最多处理 N 家公司，0=全部；配合 --offset 分片")
parser.add_argument("--pdf-limit", type=int, default=0,
                    help="每家公司最多解析 N 份 PDF，0=不限")
parser.add_argument("--latest", action="store_true",
                    help="每家公司只解析最新一份年报（已有 derived 则跳过，优先于 --pdf-limit）")
parser.add_argument("--force", action="store_true", help="重新解析已有 derived 结果的 PDF")
args = parser.parse_args()

root = Path(args.lakehouse)
markets = MARKETS if args.market == "all" else [args.market]


def _company_pending(company_dir: Path) -> bool:
    """与 ocr_company 同一队列语义：有非空待解析队列才算有活。"""
    queue = latest_pdf(company_dir, force=args.force) if args.latest \
        else pending_pdfs(company_dir, force=args.force)
    if not args.latest and args.pdf_limit > 0:
        queue = queue[:args.pdf_limit]
    return bool(queue)


def _any_pending() -> bool:
    if args.stock:
        for m in markets:
            company_dir = root / m / args.stock
            if (company_dir / "index.json").exists() and _company_pending(company_dir):
                return True
        return False
    for m in markets:
        market_dir = root / m
        if not market_dir.is_dir():
            continue
        for p in sorted(market_dir.iterdir()):
            if p.is_dir() and (p / "index.json").exists() and _company_pending(p):
                return True
    return False


if args.stock and not any((root / m / args.stock / "index.json").exists() for m in markets):
    log.error("找不到 %s 的公告目录（找过 %s）", args.stock, "、".join(markets))
    sys.exit(1)

if not _any_pending():
    log.info("没有待解析的 PDF（derived/ 已是最新），跳过 vLLM 启动")
    sys.exit(0)


def work() -> None:
    if args.stock:
        failed_all = {args.stock: ocr_stock(args.stock, markets, root, args.force,
                                            args.pdf_limit, args.latest)}
    else:
        failed_all = {m: ocr_market(m, root, args.offset, args.limit, args.force,
                                    args.pdf_limit, args.latest) for m in markets}
    total_failed = sum(len(v) for v in failed_all.values())
    if total_failed:
        log.warning("全部完成，共 %d 家公司失败: %s", total_failed,
                    {m: v for m, v in failed_all.items() if v})
        sys.exit(1)
    log.info("全部完成，无失败")


try:
    ensure_service("vllm", VLLM_CMD, VLLM_HEALTH_URL, 1800, work)
except KeyboardInterrupt:
    log.warning("收到中断，已停止（重跑同一命令即可断点续跑）")
    sys.exit(130)
