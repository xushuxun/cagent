"""年报爬虫`。

模式一：单只检索/下载（--stock 与 --market 均必填）

    # 下载单只股票年报（默认近 5 年）
    uv run cagent/scraper/cli.py --stock 09863 --market hk
    uv run cagent/scraper/cli.py --stock 600519 --market cn --from 2020-01-01

    # 只打印检索结果 JSON，不下载
    uv run cagent/scraper/cli.py --stock 09863 --market hk --search

模式二：批量下载（--list 指定股票池 JSON，--market 忽略）

    uv run cagent/scraper/cli.py --list lakehouse/auto_stocks.json
    uv run cagent/scraper/cli.py --list lakehouse/auto_stocks.json --offset 10 --limit 20 --force

股票池 JSON 格式：顶层对象取 stocks 数组，每条标的市场不同必填字段不同——

    {
      "stocks": [
        {"code": "000559", "name": "万向钱潮", "orgId": "gssz0000559", "market": "cn"},
        {"code": "00175",  "name": "吉利汽车", "market": "hk", "nameEn": "GEELY AUTO"}
      ]
    }

    code    股票代码（字符串，保留前导零），两个市场都必填
    market  cn / hk，必填（缺失或非法直接报错）
    name    显示名，可选（仅日志用）
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
from cagent.scraper.cninfo_scraper import parse_date as cn_parse_date
from cagent.scraper.cninfo_scraper import search_filings as cn_search
from cagent.scraper.hkex_scraper import download_stock as hk_download
from cagent.scraper.hkex_scraper import parse_date as hk_parse_date
from cagent.scraper.hkex_scraper import search_filings as hk_search

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
PARSE_DATE = {"cn": cn_parse_date, "hk": hk_parse_date}


def download_stocks(market: str, stocks: list[dict],
                    date_from: str, date_to: str,
                    output: Path, offset: int, limit: int, force: bool) -> list[str]:
    """用指定爬虫下载一个市场的一组标的年报，返回失败股票代码列表。"""
    total_all = len(stocks)
    stocks = stocks[offset:offset + limit if limit > 0 else None]
    if not stocks:
        log.warning("[%s] 无标的可处理（offset=%d, limit=%d，名单共 %d 只）",
                    market, offset, limit, total_all)
        return []

    log.info("=== 市场 %s: 处理第 %d~%d 只 / 名单共 %d 只 ===",
             market, offset + 1, offset + len(stocks), total_all)
    failed = []
    for i, s in enumerate(stocks, 1):
        code, name = s["code"], s.get("name", "")
        log.info("[%s %d/%d] %s %s", market, i, len(stocks), code, name)
        try:
            SCRAPERS[market](code, output, date_from, date_to, force=force)
        except Exception as exc:
            log.error("  [FAIL] %s %s（%s）", code, name, exc)
            failed.append(code)

    log.info("=== 市场 %s 完成: %d 只处理, %d 只失败 ===", market, len(stocks), len(failed))
    return failed


def group_by_market(stocks: list[dict]) -> dict[str, list[dict]]:
    """按 market 字段分组（保持 cn 在前、hk 在后，组内维持原顺序）。"""
    groups: dict[str, list[dict]] = {}
    for s in stocks:
        m = s.get("market", "")
        if m not in SCRAPERS:
            raise ValueError(f"标的 {s.get('code')} 的 market 字段无效: {m!r}（应为 cn/hk）")
        groups.setdefault(m, []).append(s)
    return {m: groups[m] for m in ("cn", "hk") if m in groups}


parser = argparse.ArgumentParser(
    description="公告下载：单只检索/下载（--stock + --market）或批量下载（--list 自定义名单）")
parser.add_argument("--stock", default="",
                    help="股票代码（单只模式必填），如 09863、600519")
parser.add_argument("--market", choices=["cn", "hk"], default="",
                    help="单只模式必填 cn/hk；批量模式忽略")
parser.add_argument("--search", action="store_true",
                    help="单只模式：只打印检索结果 JSON，不下载")
parser.add_argument("--list", dest="list_file", default="",
                    help="自定义股票池 JSON（每条含 market: cn/hk，如 lakehouse/auto_stocks.json）")
parser.add_argument("--from", dest="date_from", default="",
                    help="起始日期 YYYY-MM-DD（默认：各爬虫内置，近5年）")
parser.add_argument("--to", dest="date_to", default="", help="结束日期 YYYY-MM-DD（默认：今天）")
parser.add_argument("--output", default=str(Path(__file__).resolve().parents[2] / ".cagent"),
                    help="lakehouse 根目录（默认 <仓库根>/.cagent）")
parser.add_argument("--offset", type=int, default=0, help="每个市场内从第 N 只开始（0 起），用于分片")
parser.add_argument("--limit", type=int, default=0,
                    help="每个市场内最多处理 N 只，0=全部；配合 --offset 分片")
parser.add_argument("--force", action="store_true",
                    help="覆盖已有 PDF 并清理多余文件（透传给爬虫）")
args = parser.parse_args()

if args.stock:
    if args.market not in SCRAPERS:
        parser.error("单只模式要求 --market 指定 cn 或 hk")
    market = args.market
    if args.search:
        filings = SEARCHERS[market](
            stock_code=args.stock,
            date_from=PARSE_DATE[market](args.date_from),
            date_to=PARSE_DATE[market](args.date_to),
        )
        print(json.dumps(filings, ensure_ascii=False, indent=2))
    else:
        SCRAPERS[market](args.stock, Path(args.output), args.date_from, args.date_to,
                         force=args.force)
    sys.exit(0)

if not args.list_file:
    parser.error("批量模式要求 --list 指定股票池 JSON（单只模式用 --stock + --market）")
stocks = json.loads(Path(args.list_file).read_text(encoding="utf-8"))["stocks"]
todo = group_by_market(stocks)
log.info("自定义股票池 %s: %s", args.list_file, {m: len(s) for m, s in todo.items()})

failed_all: dict[str, list[str]] = {}
try:
    for m, stocks in todo.items():
        failed_all[m] = download_stocks(m, stocks, args.date_from, args.date_to,
                                        Path(args.output), args.offset, args.limit, args.force)
except KeyboardInterrupt:
    log.warning("收到中断，已停止（重跑同一命令即可断点续跑）")
    sys.exit(130)

    total_failed = sum(len(v) for v in failed_all.values())
    if total_failed:
        log.warning("全部完成，共 %d 只股票失败: %s", total_failed,
                    {m: v for m, v in failed_all.items() if v})
        sys.exit(1)
    log.info("全部完成，无失败")
