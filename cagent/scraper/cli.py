"""年报爬虫。

    # 下载单只股票年报（默认近 5 年）
    uv run cagent/scraper/cli.py --stock 09863 --market hk
    uv run cagent/scraper/cli.py --stock 600519 --market cn --from 2020-01-01

    # 只打印检索结果 JSON，不下载
    uv run cagent/scraper/cli.py --stock 09863 --market hk --search

    # 批量下载：按股票池 JSON（每条自带 market），分片、强制覆盖
    uv run cagent/scraper/cli.py --list lakehouse/auto_stocks.json
    uv run cagent/scraper/cli.py --list lakehouse/auto_stocks.json --offset 10 --limit 20 --force

股票池 JSON 格式：顶层对象取 stocks 数组，每条标的必填 code 和 market——

    {
      "stocks": [
        {"code": "000559", "name": "万向钱潮", "orgId": "gssz0000559", "market": "cn"},
        {"code": "00175",  "name": "吉利汽车", "market": "hk", "nameEn": "GEELY AUTO"}
      ]
    }

    orgId   巨潮搜索参数，cn 建议填（缺了回退只传 code，搜索可能不准）；hk 不需要
    nameEn  港股英文名，可选（当前未使用）

批量语义：幂等断点续跑（已存在的 PDF 自动跳过）；单只失败只记日志、不中断
批次；全部结束后输出失败清单，有失败则以退出码 1 结束。
"""

import argparse
import json
import logging
import sys
from pathlib import Path

from cagent.scraper.cninfo_scraper import download_stock as cn_download
from cagent.scraper.cninfo_scraper import search_filings as cn_search
from cagent.scraper.hkex_scraper import download_stock as hk_download
from cagent.scraper.hkex_scraper import search_filings as hk_search
from cagent.scraper.lakehouse_index import parse_date

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stderr,
)
logging.getLogger("urllib3").setLevel(logging.WARNING)
log = logging.getLogger("scraper")

SCRAPERS = {"cn": cn_download, "hk": hk_download}
SEARCHERS = {"cn": cn_search, "hk": hk_search}


def group_by_market(stocks: list[dict]) -> dict[str, list[dict]]:
    """按 market 字段分组（保持 cn 在前、hk 在后，组内维持原顺序）。"""
    bad = [s for s in stocks if s.get("market", "") not in SCRAPERS]
    if bad:
        raise ValueError(f"标的 {bad[0].get('code')} 的 market 字段无效: {bad[0].get('market')!r}（应为 cn/hk）")
    return {m: [s for s in stocks if s["market"] == m] for m in ("cn", "hk") if any(s["market"] == m for s in stocks)}


def _try_download(market: str, s: dict, i: int, total: int) -> str | None:
    """下载单只标的，失败记日志并返回股票代码，成功返回 None。"""
    code, name = s["code"], s.get("name", "")
    log.info(f"[{market} {i}/{total}] {code} {name}")
    try:
        SCRAPERS[market](code, Path(args.output), args.date_from, args.date_to, force=args.force)
        return None
    except Exception as exc:
        log.error(f"  [FAIL] {code} {name}（{exc}）")
        return code


def download_stocks(market: str, stocks: list[dict]) -> list[str]:
    """用指定爬虫下载一个市场的一组标的年报，返回失败股票代码列表。"""
    stocks = stocks[args.offset : args.offset + args.limit if args.limit > 0 else None]
    if not stocks:
        log.warning(f"[{market}] 无标的可处理（offset={args.offset}, limit={args.limit}）")
        return []
    log.info(f"=== 市场 {market}: 处理 {len(stocks)} 只 ===")
    failed = [code for i, s in enumerate(stocks, 1) if (code := _try_download(market, s, i, len(stocks)))]
    log.info(f"=== 市场 {market} 完成: {len(stocks)} 只处理, {len(failed)} 只失败 ===")
    return failed


def run_single() -> int:
    """单只模式：--search 只打印检索 JSON，否则下载。"""
    if args.market not in SCRAPERS:
        parser.error("单只模式要求 --market 指定 cn 或 hk")
    if args.search:
        filings = SEARCHERS[args.market](args.stock, parse_date(args.date_from), parse_date(args.date_to))
        print(json.dumps(filings, ensure_ascii=False, indent=2))
    else:
        SCRAPERS[args.market](
            args.stock,
            Path(args.output),
            args.date_from,
            args.date_to,
            force=args.force,
        )
    return 0


def run_batch() -> int:
    """批量模式：读股票池 → 按市场分组 → 逐市场下载 → 汇总失败。"""
    if not args.list_file:
        parser.error("批量模式要求 --list 指定股票池 JSON（单只模式用 --stock + --market）")
    stocks = json.loads(Path(args.list_file).read_text(encoding="utf-8"))["stocks"]
    todo = group_by_market(stocks)
    log.info(f"自定义股票池 {args.list_file}: { {m: len(s) for m, s in todo.items()} }")

    try:
        failed = {m: download_stocks(m, group) for m, group in todo.items()}
    except KeyboardInterrupt:
        log.warning("收到中断，已停止（重跑同一命令即可断点续跑）")
        return 130

    if any(failed.values()):
        log.warning(f"全部完成，共 {sum(map(len, failed.values()))} 只股票失败: { {m: v for m, v in failed.items() if v} }")
        return 1
    log.info("全部完成，无失败")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="公告下载：单只检索/下载（--stock + --market）或批量下载（--list 自定义名单）")
    parser.add_argument("--stock", default="", help="股票代码（单只模式必填），如 09863、600519")
    parser.add_argument(
        "--market",
        choices=["cn", "hk"],
        default="",
        help="单只模式必填 cn/hk；批量模式忽略",
    )
    parser.add_argument("--search", action="store_true", help="单只模式：只打印检索结果 JSON，不下载")
    parser.add_argument(
        "--list",
        dest="list_file",
        default="",
        help="自定义股票池 JSON（每条含 market: cn/hk，如 lakehouse/auto_stocks.json）",
    )
    parser.add_argument(
        "--from",
        dest="date_from",
        default="",
        help="起始日期 YYYY-MM-DD（默认：各爬虫内置，近5年）",
    )
    parser.add_argument("--to", dest="date_to", default="", help="结束日期 YYYY-MM-DD（默认：今天）")
    parser.add_argument(
        "--output",
        default=str(Path(__file__).resolve().parents[2] / ".cagent"),
        help="lakehouse 根目录（默认 <仓库根>/.cagent）",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="每个市场内从第 N 只开始（0 起），用于分片",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="每个市场内最多处理 N 只，0=全部；配合 --offset 分片",
    )
    parser.add_argument("--force", action="store_true", help="覆盖已有 PDF 并清理多余文件（透传给爬虫）")
    args = parser.parse_args()

    sys.exit(run_single() if args.stock else run_batch())
