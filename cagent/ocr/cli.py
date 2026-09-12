"""OCR cli

    uv run cagent/ocr/cli.py --stock 09863 --market hk [--latest] [--pdf-limit N] [--force]
    
"""

import argparse
import json
import logging
import sys
from pathlib import Path

from cagent.ocr.convert import process_pdf
from cagent.service import OCR, ensure_service

MARKETS = ["cn", "hk"]

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
parser.add_argument("--lakehouse", default=str(Path(__file__).resolve().parents[2] / ".cagent"),
                    help="lakehouse 根目录（默认 <仓库根>/.cagent）")
parser.add_argument("--offset", type=int, default=0, help="从第 N 家公司开始（0 起），用于分片")
parser.add_argument("--limit", type=int, default=0,
                    help="最多处理 N 家公司，0=全部；配合 --offset 分片")
parser.add_argument("--pdf-limit", type=int, default=0,
                    help="每家公司最多解析 N 份 PDF，0=不限")
parser.add_argument("--latest", action="store_true",
                    help="每家公司只解析最新一份年报（已有 derived 则跳过，优先于 --pdf-limit）")
parser.add_argument("--force", action="store_true", help="重新解析已有 derived 结果的 PDF")
parser.add_argument("--extract-images", action="store_true",
                    help="裁剪保存图表图片到 derived/images/<文档名>/（默认关闭）")
args = parser.parse_args()

root = Path(args.lakehouse)
markets = MARKETS if args.market == "all" else [args.market]


def company_queue(company_dir: Path) -> list[Path]:
    """待解析队列：index.json 中**年报**的 file 减去 derived/ 已有产物（幂等，--force 重跑）。

    --latest：只取最新一份；它已有 derived 时返回空（不回头补解析旧年报）。
    --pdf-limit：截断到前 N 份。
    """
    index_path = company_dir / "index.json"
    if not index_path.exists():
        raise FileNotFoundError(
            f"未找到 {index_path}，请先用下载环节拉取公告（见 cagent/scraper/）")
    filings = json.loads(index_path.read_text(encoding="utf-8")).get("filings", [])
    pdfs: list[Path] = []
    for filing in filings:
        pdf_path = company_dir / filing["file"]
        if not pdf_path.exists():
            log.warning("[SKIP] raw PDF 缺失: %s", pdf_path)
            continue
        pdfs.append(pdf_path)

    derived = company_dir / "derived"

    def parsed(pdf: Path) -> bool:
        return (derived / (pdf.stem + ".md")).exists()

    if args.latest:
        if not pdfs or (parsed(pdfs[-1]) and not args.force):
            return []
        return [pdfs[-1]]
    queue = [p for p in pdfs if args.force or not parsed(p)]
    return queue[:args.pdf_limit] if args.pdf_limit > 0 else queue


def company_dirs() -> list[Path]:
    """本次要处理的公司目录：单家模式找含 index.json 的市场目录；批量模式按市场切片。"""
    if args.stock:
        dirs = [root / m / args.stock for m in markets
                if (root / m / args.stock / "index.json").exists()]
        if not dirs:
            log.error("找不到 %s 的公告目录（找过 %s）", args.stock, "、".join(markets))
            sys.exit(1)
        return dirs
    dirs: list[Path] = []
    for m in markets:
        market_dir = root / m
        if not market_dir.is_dir():
            log.warning("[%s] 目录不存在: %s（先跑批量下载：uv run python cagent/scraper/cli.py）",
                        m, market_dir)
            continue
        companies = sorted(p for p in market_dir.iterdir()
                           if p.is_dir() and (p / "index.json").exists())
        dirs += companies[args.offset:args.offset + args.limit if args.limit > 0 else None]
    return dirs


# 建计划：先算好所有待解析队列，空的整批直接退出（不启动服务）；队列算错的公司记失败
plan: list[tuple[Path, list[Path]]] = []
failed: list[str] = []
for d in company_dirs():
    try:
        queue = company_queue(d)
    except Exception as exc:
        log.error("[FAIL] %s（%s）", d.relative_to(root), exc)
        failed.append(str(d.relative_to(root)))
        continue
    if queue:
        plan.append((d, queue))

if not plan:
    log.info("没有待解析的 PDF（derived/ 已是最新），跳过 vLLM 启动")
    sys.exit(0 if not failed else 1)


def work() -> None:
    for company_dir, queue in plan:
        log.info("=== %s：待解析 %d 份 ===", company_dir.relative_to(root), len(queue))
        fail = 0
        for i, pdf_path in enumerate(queue, 1):
            log.info("解析第 %d/%d 份: %s", i, len(queue), pdf_path.name)
            try:
                ok = process_pdf(pdf_path, company_dir,
                                 extract_images=args.extract_images)
            except Exception as exc:
                log.error("[FAIL] %s: %s", pdf_path, exc)
                ok = False
            if not ok:
                fail += 1
        if fail:
            failed.append(f"{company_dir.relative_to(root)}（{fail} 份解析失败）")
    if failed:
        log.warning("全部完成，共 %d 家公司失败: %s", len(failed), failed)
        sys.exit(1)
    log.info("全部完成，无失败")


if __name__ == "__main__":
    try:
        ensure_service(OCR, work)
    except KeyboardInterrupt:
        log.warning("收到中断，已停止（重跑同一命令即可断点续跑）")
        sys.exit(130)
