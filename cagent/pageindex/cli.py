"""页码索引批量构建入口：`uv run python cagent/pageindex/cli.py ...`。

遍历 lakehouse 下已有的公司目录，为 derived/ 下缺 `.pageindex.json` 的公告构建
页码索引（文档构建产物）。llama-server 自管：probe 到在跑的直接复用，没有的按
lib 服务层同款命令拉起、跑完即关；全部已有索引时直接退出，不启动服务。
幂等：已有索引自动跳过（--force 重建）；单家失败只记日志不中断批次。

    uv run python cagent/pageindex/cli.py --market all [--offset N] [--limit N] [--force]
"""

import argparse
import logging
import sys
from pathlib import Path

from cagent.lib.lakehouse import LakehouseReader
from cagent.lib.service import LLAMA_CMD, LLAMA_HEALTH_URL, ensure_service
from cagent.pageindex import (
    DEFAULT_LAKEHOUSE,
    MARKETS,
    pageindex_is_current,
    pageindex_market,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stderr,
)
logging.getLogger("urllib3").setLevel(logging.WARNING)
log = logging.getLogger("pageindex")

parser = argparse.ArgumentParser(
    description="批量构建全部标的的年报页码索引（dspy 流水线）")
parser.add_argument("--market", choices=["all", "cn", "hk"], default="all",
                    help="目标市场：cn=A股, hk=港股, all=两者（默认）")
parser.add_argument("--lakehouse", default=str(DEFAULT_LAKEHOUSE),
                    help="lakehouse 根目录（默认 <仓库根>/.cagent）")
parser.add_argument("--offset", type=int, default=0, help="从第 N 家公司开始（0 起），用于分片")
parser.add_argument("--limit", type=int, default=0,
                    help="最多处理 N 家公司，0=全部；配合 --offset 分片")
parser.add_argument("--force", action="store_true", help="重建已有 .pageindex.json")
args = parser.parse_args()

root = Path(args.lakehouse)
markets = MARKETS if args.market == "all" else [args.market]


def _market_pending(market: str) -> bool:
    """该市场是否有任何公司缺 .pageindex.json（与 pageindex_company 同一判定）。"""
    reader = LakehouseReader(root=root)
    market_dir = root / market
    if not market_dir.is_dir():
        return False
    for p in sorted(market_dir.iterdir()):
        if not (p.is_dir() and (p / "index.json").exists()):
            continue
        data_dir = reader.company_dir(market, p.name)
        derived = data_dir / "derived"
        if not derived.is_dir():
            continue
        for f in reader.list_filings(data_dir):
            if args.force or not pageindex_is_current(derived / f["file"], reader):
                return True
    return False


if not any(_market_pending(m) for m in markets):
    log.info("没有缺 .pageindex.json 的公告，跳过 llama-server 启动")
    sys.exit(0)


def work() -> None:
    failed_all: dict[str, list[str]] = {}
    for m in markets:
        failed_all[m] = pageindex_market(m, root, args.offset, args.limit, args.force)
    total_failed = sum(len(v) for v in failed_all.values())
    if total_failed:
        log.warning("全部完成，共 %d 家公司失败: %s", total_failed,
                    {m: v for m, v in failed_all.items() if v})
        sys.exit(1)
    log.info("全部完成，无失败")


try:
    ensure_service("llama-server", LLAMA_CMD, LLAMA_HEALTH_URL, 1800, work)
except KeyboardInterrupt:
    log.warning("收到中断，已停止（重跑同一命令即可断点续跑）")
    sys.exit(130)
