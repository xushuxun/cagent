r"""CNInfo Annual Report Scraper - 巨潮资讯网A股年报检索与下载（库模块）。

直接调用巨潮资讯网公开 JSON API（按年报分类服务端过滤），无需浏览器。
进度日志输出到 stderr。CLI 入口在本包 cli.py
（`uv run cagent/scraper/cli.py --stock <code> --market cn`），本模块只提供
函数：search_filings（检索）/ download_stock（单只下载）。
"""

import json
import logging
import re
import time
from collections import Counter
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path

import requests

from cagent.scraper.lakehouse_index import (
    atomic_write,
    cleanup_orphans,
    parse_date,
    upsert_index,
)

log = logging.getLogger("cninfo")

CNINFO_SEARCH_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_STOCK_URL = "https://www.cninfo.com.cn/new/data/szse_stock.json"
CNINFO_PDF_BASE = "https://static.cninfo.com.cn/"

# 本地股票列表（lakehouse/cn_stocks.json，含 orgId），存在则免走 API。
# 批量下载时每只股票一个子进程，进程内缓存挡不住反复拉取，故优先读本地文件。
LOCAL_STOCK_LIST = Path(__file__).resolve().parent.parent.parent / "lakehouse" / "cn_stocks.json"

# 请求间隔 1.5s：巨潮对限速抓得很紧
REQUEST_INTERVAL = 1.5

DEFAULT_HEADERS = {
    "Accept": "*/*",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    # 不带 AppleWebKit/Chrome/Safari 版本段：完整 Chrome UA 会被 cninfo WAF 403，
    # 短 UA 放行（实测 2026-09）。
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Origin": "https://www.cninfo.com.cn",
    "Referer": "https://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search",
}

# 年报分类代码（服务端过滤）
ANNUAL_REPORT_CATEGORY = "category_ndbg_szsh"


def is_annual_report(title: str) -> bool:
    """标题含“年度报告”，且排除摘要、英文版等非正文文件。"""
    return "年度报告" in title and "摘要" not in title and "英文" not in title


@lru_cache(maxsize=1)
def _stock_list() -> dict[str, dict]:
    """加载股票代码 -> orgId 映射，优先读本地 lakehouse 副本，没有再拉巨潮 API。"""
    if LOCAL_STOCK_LIST.exists():
        data = json.loads(LOCAL_STOCK_LIST.read_text(encoding="utf-8"))
        stocks = {s["code"].strip(): s for s in data.get("stocks", []) if s.get("code", "").strip()}
        log.info(f"已从本地 {LOCAL_STOCK_LIST.name} 加载 {len(stocks)} 只A股")
        return stocks
    log.info("加载A股股票列表...")
    resp = requests.get(CNINFO_STOCK_URL, timeout=30, headers={
        "User-Agent": DEFAULT_HEADERS["User-Agent"],
        "Referer": "https://www.cninfo.com.cn/",
    })
    resp.raise_for_status()
    return {s["code"].strip(): {"orgId": s.get("orgId", ""), "name": s.get("zwjc", "")}
            for s in resp.json().get("stockList", []) if s.get("code", "").strip()}


def _build_stock_param(code: str) -> str:
    """拼巨潮 API 的 stock 参数：要求 'code,orgId' 格式，查不到 orgId 就只传 code。"""
    info = _stock_list().get(code.strip(), {})
    return f"{code},{info['orgId']}" if info.get("orgId") else code


def _fetch_page(stock_param: str, se_date: str, page_num: int) -> dict:
    payload = {
        "pageNum": str(page_num),
        "pageSize": "30",
        "column": "szse",  # 巨潮全站检索的历史沿用值
        "tabName": "fulltext",
        "plate": "",
        "stock": stock_param,
        "searchkey": "",
        "secid": "",
        "category": ANNUAL_REPORT_CATEGORY,
        "trade": "",
        "seDate": se_date,
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true",
    }
    resp = requests.post(CNINFO_SEARCH_URL, data=payload, headers=DEFAULT_HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _parse_announcement(ann: dict) -> dict:
    """原始公告记录 -> 统一字段：剥掉搜索高亮 <em> 标签，毫秒时间戳转 ISO 日期。"""
    adjunct_url = ann.get("adjunctUrl", "")
    ann_time_ms = ann.get("announcementTime", 0)
    return {
        "secCode": ann.get("secCode", "").strip(),
        "secName": re.sub(r"</?em>", "", ann.get("secName", "")).strip(),
        "title": re.sub(r"</?em>", "", ann.get("announcementTitle", "")).strip(),
        "announcementDate": datetime.fromtimestamp(ann_time_ms / 1000).strftime("%Y-%m-%d") if ann_time_ms else "",
        "adjunctUrl": adjunct_url,
        "adjunctSize": ann.get("adjunctSize", 0),
        "adjunctType": ann.get("adjunctType", ""),
        "pdfUrl": f"{CNINFO_PDF_BASE}{adjunct_url}" if adjunct_url else "",
    }


def fetch_annual_reports(stock: str, date_from: str, date_to: str) -> list[dict]:
    """拉取年报公告（服务端按年报分类过滤），翻页直到取完。"""
    stock_param, se_date = _build_stock_param(stock), f"{date_from}~{date_to}"
    page1 = _fetch_page(stock_param, se_date, 1)
    total_pages = page1.get("totalpages", 1)
    log.info(f"共 {page1.get('totalRecordNum', 0)} 条公告，{total_pages} 页")

    pages = [page1]
    for page_num in range(2, total_pages + 1):
        time.sleep(REQUEST_INTERVAL)
        log.info(f"请求第 {page_num}/{total_pages} 页...")
        pages.append(_fetch_page(stock_param, se_date, page_num))

    announcements = [a for p in pages for a in p.get("announcements") or []]
    if not announcements:
        log.info("未找到公告")
    return [_parse_announcement(a) for a in announcements]


def search_filings(stock_code: str, date_from: datetime | None = None,
                   date_to: datetime | None = None) -> list[dict]:
    """搜索年报元数据（服务端按年报分类过滤，再按标题排除摘要/英文版）。"""
    today = datetime.now()
    date_from, date_to = date_from or today - timedelta(days=5 * 365), date_to or today
    log.info(f"开始搜索: 股票={stock_code}, 区间 {date_from:%Y-%m-%d} ~ {date_to:%Y-%m-%d}")

    announcements = fetch_annual_reports(stock_code, date_from.strftime("%Y-%m-%d"),
                                         date_to.strftime("%Y-%m-%d"))
    kept = [r for r in announcements
            if r["adjunctType"].upper() == "PDF" and is_annual_report(r["title"])]
    log.info(f"搜索完成：命中 {len(announcements)} 条，滤除非 PDF 与摘要/英文版后保留 {len(kept)} 条")
    return kept


def _filename(f: dict) -> str:
    """公告文件名：ISO 日期前缀 + 数据源原文件名（见 lakehouse/README.md 命名规则）。"""
    orig = f.get("adjunctUrl", "").split("/")[-1] or "download.pdf"
    return f"{f.get('announcementDate', 'nodate')}_{orig}"


def _download_one(sess: requests.Session, f: dict, root: Path, market: str,
                  force: bool, i: int, total: int) -> str:
    """下载单条公告 PDF 并补登 index 条目，返回 ok/skip/fail。"""
    if not f.get("pdfUrl"):
        log.warning(f"[SKIP] 无链接: {f.get('title', '')}")
        return "fail"
    sec_code = f.get("secCode", "unknown")
    company_dir = root / market / sec_code
    path = company_dir / _filename(f)
    entry = {
        "date": f.get("announcementDate", "nodate"),
        "title": f["title"],
        "file": path.name,
        "link": f["pdfUrl"],
        "fileType": f.get("adjunctType", "PDF"),
        "fileSize": f.get("adjunctSize", 0),
        "secCode": sec_code,
        "secName": f.get("secName", ""),
    }
    try:
        if path.exists() and not force:
            log.info(f"[SKIP] 已存在: {path}")
            result = "skip"
        else:
            log.info(f"{'覆盖下载' if force and path.exists() else '下载'}第 {i}/{total} 个: {f['title']}")
            company_dir.mkdir(parents=True, exist_ok=True)
            resp = sess.get(f["pdfUrl"], timeout=60, headers={
                "User-Agent": DEFAULT_HEADERS["User-Agent"],
                "Referer": "https://www.cninfo.com.cn/",
            })
            resp.raise_for_status()
            content_type = resp.headers.get("Content-Type", "")
            if "pdf" not in content_type.lower() and not resp.content[:5].startswith(b"%PDF"):
                log.error(f"[FAIL] 不是 PDF: {path.name} (ct={content_type[:30]})")
                return "fail"
            atomic_write(path, resp.content)
            log.info(f"[OK] {path}")
            time.sleep(REQUEST_INTERVAL)
            result = "ok"
        upsert_index(company_dir, market, sec_code, f.get("secName", ""), entry)
        return result
    except Exception as e:
        log.error(f"[FAIL] {f['pdfUrl']}: {e}")
        return "fail"


def download_filings(filings: list[dict], root: Path, market: str, force: bool = False) -> None:
    """按 lakehouse 规范（仓库 lakehouse/README.md）串行下载年报 PDF 并维护 index.json。

    增量安全（默认）：不覆盖已有 PDF、不删本地条目、合并保留本地额外字段；
    幂等：按原文件名去重，已存在的文件跳过下载但仍补登 index 条目；
    原子写：PDF 与 index.json 均先写临时文件再改名，中断不留半截文件。

    --force 模式：覆盖已有 PDF 并清理本地多余文件，使本地目录与搜索结果精确同步。
    """
    sess = requests.Session()
    stats = Counter(_download_one(sess, f, root, market, force, i, len(filings))
                    for i, f in enumerate(filings, 1))

    if force and filings:
        company_dir = root / market / filings[0].get("secCode", "unknown")
        expected = {_filename(f) for f in filings if f.get("pdfUrl")}
        if removed := cleanup_orphans(company_dir, expected):
            log.info(f"已清理 {removed} 个多余 PDF 文件")

    log.info(f"下载完成: {stats['ok']} 新下载, {stats['skip']} 已存在跳过, {stats['fail']} 失败")


def download_stock(stock_code: str, output: Path, date_from: str = "", date_to: str = "",
                   force: bool = False) -> None:
    """搜索并下载单只股票的年报到 lakehouse（函数调用入口，供批量脚本使用）。

    日期参数为 YYYY-MM-DD 字符串，空串表示用内置默认（近5年起、至今）。
    """
    filings = search_filings(stock_code, parse_date(date_from), parse_date(date_to))
    log.info(f"共找到 {len(filings)} 条年报")
    download_filings(filings, output, "cn", force=force)
