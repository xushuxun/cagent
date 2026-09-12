"""公告下载入口（单只检索/下载 + 批量编排）：`uv run python cagent/scraper/cli.py ...`。

单只（原各爬虫模块的 CLI 能力收拢到这里）：
    uv run python cagent/scraper/cli.py --stock 09863 --market hk
    uv run python cagent/scraper/cli.py --stock 600519 --market cn --from 2020-01-01
    uv run python cagent/scraper/cli.py --stock 09863 --market hk --search  # 只打印检索结果 JSON
批量（内置股票池或自定义名单，幂等断点续跑）：
    uv run python cagent/scraper/cli.py --market all [--offset N] [--limit N] [--force]
    uv run python cagent/scraper/cli.py --list lakehouse/auto_stocks.json
"""

import argparse
import json
import logging
import sys
from pathlib import Path

from cagent.scraper import DEFAULT_OUTPUT, STOCK_LISTS, download_stocks, group_by_market
from cagent.scraper.cninfo_scraper import (
    download_stock as cn_download,
)
from cagent.scraper.cninfo_scraper import (
    parse_date as cn_parse_date,
)
from cagent.scraper.cninfo_scraper import (
    search_filings as cn_search,
)
from cagent.scraper.hkex_scraper import (
    download_stock as hk_download,
)
from cagent.scraper.hkex_scraper import (
    parse_date as hk_parse_date,
)
from cagent.scraper.hkex_scraper import (
    search_filings as hk_search,
)

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

parser = argparse.ArgumentParser(
    description="公告下载：单只检索/下载（--stock）或批量下载全部标的（内置/自定义名单）")
parser.add_argument("--stock", default="",
                    help="股票代码（单只模式必填），如 09863、600519")
parser.add_argument("--market", choices=["all", "cn", "hk"], default="all",
                    help="单只模式必填 cn/hk；批量模式为目标市场（默认 all）")
parser.add_argument("--search", action="store_true",
                    help="单只模式：只打印检索结果 JSON，不下载")
parser.add_argument("--list", dest="list_file", default="",
                    help="自定义股票池 JSON（每条含 market: cn/hk，如 lakehouse/auto_stocks.json）")
parser.add_argument("--from", dest="date_from", default="",
                    help="起始日期 YYYY-MM-DD（默认：各爬虫内置，近5年）")
parser.add_argument("--to", dest="date_to", default="", help="结束日期 YYYY-MM-DD（默认：今天）")
parser.add_argument("--output", default=str(DEFAULT_OUTPUT),
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

if args.list_file:
    stocks = json.loads(Path(args.list_file).read_text(encoding="utf-8"))["stocks"]
    todo = group_by_market(stocks)
    log.info("自定义股票池 %s: %s", args.list_file, {m: len(s) for m, s in todo.items()})
else:
    markets = ["cn", "hk"] if args.market == "all" else [args.market]
    todo = {m: json.loads(STOCK_LISTS[m].read_text(encoding="utf-8"))["stocks"]
            for m in markets}

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
