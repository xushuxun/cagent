"""公告下载包：单只抓取（cninfo / hkex 爬虫模块）+ 批量编排（batch.py）。

CLI 入口是本包的 cli.py（`uv run python cagent/scraper/cli.py ...`）；包内模块一律
绝对导入（`cagent.*`），禁用相对导入与手工 sys.path。
"""

from cagent.lib.lakehouse import DEFAULT_LAKEHOUSE as DEFAULT_OUTPUT
from cagent.scraper.batch import SCRAPERS, STOCK_LISTS, download_stocks, group_by_market

__all__ = [
    "DEFAULT_OUTPUT",
    "SCRAPERS",
    "STOCK_LISTS",
    "download_stocks",
    "group_by_market",
]