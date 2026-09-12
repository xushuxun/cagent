"""页码索引（pageindex）包：构建（dspy 流水线）与读取（纯读取、不碰 LLM）。

批量构建入口是本包的 cli.py（`uv run python cagent/pageindex/cli.py`）；读取：`reader.py`。
"""

from cagent.lib.lakehouse import DEFAULT_LAKEHOUSE
from cagent.pageindex.builder import (
    PageIndexBuilder,
    pageindex_company,
    pageindex_market,
)
from cagent.pageindex.reader import (
    PAGEINDEX_SUFFIX,
    PageIndexReader,
    pageindex_is_current,
)

MARKETS = ["cn", "hk"]

__all__ = [
    "DEFAULT_LAKEHOUSE",
    "MARKETS",
    "PAGEINDEX_SUFFIX",
    "PageIndexBuilder",
    "PageIndexReader",
    "pageindex_company",
    "pageindex_is_current",
    "pageindex_market",
]