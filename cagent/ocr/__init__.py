"""cagent 分析主体包。

入口是各包内的 cli.py 与 cagent/report.py（`uv run python cagent/<入口>.py`，见
各脚本 docstring 与 AGENTS.md）；包内模块一律绝对导入（`cagent.*`），禁用相对
导入与手工 sys.path。
"""

from cagent.lib.lakehouse import DEFAULT_LAKEHOUSE as LAKEHOUSE_ROOT
from cagent.ocr.batch import ocr_company, ocr_market, ocr_stock, run
from cagent.ocr.convert import (
    clean_grounded_markdown,
    process_pdf,
    request_page_markdown,
)
from cagent.ocr.pending import is_annual_report, latest_pdf, pending_pdfs

MARKETS = ["cn", "hk"]

__all__ = [
    "LAKEHOUSE_ROOT",
    "MARKETS",
    "clean_grounded_markdown",
    "is_annual_report",
    "latest_pdf",
    "ocr_company",
    "ocr_market",
    "ocr_stock",
    "pending_pdfs",
    "process_pdf",
    "request_page_markdown",
    "run",
]