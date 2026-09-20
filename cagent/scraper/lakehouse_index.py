"""lakehouse raw 层写入工具：两个市场爬虫共用。

规范见仓库 lakehouse/README.md。index.json 日期字段统一用 `date`；
A股存量数据用的是 `announcementDate`（已知偏差），读取时两边都要认。
"""

import json
import logging
from datetime import datetime
from pathlib import Path

log = logging.getLogger("lakehouse_index")


def parse_date(s: str) -> datetime | None:
    """解析 YYYY-MM-DD 日期。"""
    return datetime.strptime(s, "%Y-%m-%d") if s else None


def atomic_write(path: Path, data: bytes) -> None:
    """先写临时文件再改名，避免进程中断留下半截文件。"""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def _filing_date(record: dict) -> str:
    """公告日期：新数据 `date`，A股存量 `announcementDate`，都缺回落文件名前 10 字符（ISO 前缀）。"""
    return record.get("date") or record.get("announcementDate") or record.get("file", "")[:10]


def _dump_index(idx_path: Path, index: dict) -> None:
    atomic_write(
        idx_path,
        (json.dumps(index, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )


def upsert_index(company_dir: Path, market: str, code: str, name: str, entry: dict) -> None:
    """向公司 index.json 插入/更新一条公告条目，保持 filings 按日期升序。

    增量安全：合并而非替换已有条目，保留本地额外字段（如手工批注）；原子写防损坏。
    """
    idx_path = company_dir / "index.json"
    index = json.loads(idx_path.read_text(encoding="utf-8")) if idx_path.exists() else {"market": market, "stockCode": code, "stockName": name, "filings": []}
    old = [r for r in index["filings"] if isinstance(r, dict)]  # 跳过脏数据
    entry = {**next((r for r in old if r["file"] == entry["file"]), {}), **entry}
    index["filings"] = sorted([r for r in old if r["file"] != entry["file"]] + [entry], key=_filing_date)
    _dump_index(idx_path, index)


def cleanup_orphans(company_dir: Path, expected_filenames: set[str]) -> int:
    """删除本地多余 PDF 文件并更新 index.json，返回删除数量。"""
    idx_path = company_dir / "index.json"
    orphans = [f for f in company_dir.iterdir() if f.is_file() and f.name != "index.json" and f.name not in expected_filenames]
    for f in orphans:
        log.info(f"  删除多余文件: {f.name}")
        f.unlink()
    if orphans and idx_path.exists():
        index = json.loads(idx_path.read_text(encoding="utf-8"))
        kept = [r for r in index.get("filings", []) if r.get("file") in expected_filenames]
        if len(kept) < len(index.get("filings", [])):
            log.info(f"  index.json 已清理 {len(index['filings']) - len(kept)} 条旧条目")
            index["filings"] = kept
            _dump_index(idx_path, index)
    return len(orphans)
