"""OCR 队列判定：年报识别与待解析队列（只读 index.json，与 PDF 引擎无关）。

对应——lakehouse 约定（见 lakehouse/README.md）：raw 层 PDF + index.json，
derived/ 是同名 .md 的解析产物；待解析队列 = 索引里**年报**的文件减去 derived/
已有文件（幂等，--force 可重跑）。
"""

import json
import logging
from pathlib import Path

from cagent.lib.lakehouse import is_annual_report

log = logging.getLogger("ocr.pending")


def atomic_write(path: Path, data: str) -> None:
    """先写临时文件再改名，避免进程中断留下半截文件。"""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(data, encoding="utf-8")
    tmp.replace(path)


def pending_pdfs(company_dir: Path, force: bool = False) -> list[Path]:
    """待 OCR 队列：index.json 中**年报**的 file 列表减去 derived/ 已有文件。"""
    index_path = company_dir / "index.json"
    if not index_path.exists():
        raise FileNotFoundError(
            f"未找到 {index_path}，请先用下载环节拉取公告（见 cagent/scraper/）")
    index = json.loads(index_path.read_text(encoding="utf-8"))
    derived_dir = company_dir / "derived"
    queue: list[Path] = []
    for filing in index.get("filings", []):
        if not is_annual_report(filing.get("title", "")):
            continue
        pdf_path = company_dir / filing["file"]
        if not pdf_path.exists():
            log.warning("[SKIP] raw PDF 缺失: %s", pdf_path)
            continue
        md_path = derived_dir / (pdf_path.stem + ".md")
        if md_path.exists() and not force:
            continue
        queue.append(pdf_path)
    return queue


def latest_pdf(company_dir: Path, force: bool = False) -> list[Path]:
    """只取最新一份年报（在年报候选中按公告日期/文件名序取最后一份）。

    幂等：该份年报已有 derived 结果时返回空队列（不回头补解析旧年报）。
    若它从未解析，即使更晚的 ESG/通知信函已解析，也会正确返回年报本身。
    """
    queue = pending_pdfs(company_dir, force=True)  # 先拿全量（含已解析），再按最新一条判断
    if not queue:
        return []
    newest = queue[-1]
    md_path = company_dir / "derived" / (newest.stem + ".md")
    if md_path.exists() and not force:
        return []
    return [newest]