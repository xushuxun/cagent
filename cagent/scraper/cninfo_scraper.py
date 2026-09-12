r"""CNInfo Annual Report Scraper - 巨潮资讯网A股年报检索与下载（库模块）。

直接调用巨潮资讯网公开 JSON API（按年报分类服务端过滤），无需浏览器。
进度日志输出到 stderr。CLI 入口在本包 cli.py
（`uv run python cagent/scraper/cli.py --stock <code> --market cn`），本模块只提供
函数：search_filings（检索）/ download_stock（单只下载）。
"""

import json
import logging
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stderr,
)
log: logging.Logger = logging.getLogger("cninfo")

CNINFO_SEARCH_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_STOCK_URL = "https://www.cninfo.com.cn/new/data/szse_stock.json"
CNINFO_PDF_BASE = "https://static.cninfo.com.cn/"

# 本地股票列表（lakehouse/cn_stocks.json，含 orgId），存在则免走 API。
# 批量下载时每只股票一个子进程，进程内缓存挡不住反复拉取，故优先读本地文件。
LOCAL_STOCK_LIST = Path(__file__).resolve().parent.parent.parent / "lakehouse" / "cn_stocks.json"

# Rate limit: 1.5s between requests (cninfo is aggressive on rate limiting)
REQUEST_INTERVAL = 1.5

DEFAULT_HEADERS = {
    "Accept": "*/*",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Origin": "https://www.cninfo.com.cn",
    "Referer": "https://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search",
}

# 年报分类代码（服务端过滤）
ANNUAL_REPORT_CATEGORY = "category_ndbg_szsh"


def is_annual_report(title: str) -> bool:
    """标题含“年度报告”，且排除摘要、英文版等非正文文件。"""
    return "年度报告" in title and "摘要" not in title and "英文" not in title


# ---------------------------------------------------------------------------
# Stock code -> orgId mapping
# ---------------------------------------------------------------------------

_stock_cache: dict[str, dict] | None = None


def _load_stock_list() -> dict[str, dict]:
    """Load stock list with orgId, preferring the local lakehouse copy; falls back to cninfo API."""
    global _stock_cache
    if _stock_cache is not None:
        return _stock_cache

    if LOCAL_STOCK_LIST.exists():
        try:
            data = json.loads(LOCAL_STOCK_LIST.read_text(encoding="utf-8"))
            _stock_cache = {
                s["code"].strip(): {"code": s["code"].strip(), "orgId": s.get("orgId", ""), "name": s.get("name", "")}
                for s in data.get("stocks", []) if s.get("code", "").strip()
            }
            log.info("已从本地 %s 加载 %d 只A股", LOCAL_STOCK_LIST.name, len(_stock_cache))
            return _stock_cache
        except Exception as e:
            log.warning("本地股票列表读取失败（%s），回退到 cninfo API", e)

    log.info("加载A股股票列表...")
    _stock_cache = {}

    try:
        resp = requests.get(
            CNINFO_STOCK_URL,
            headers={
                "User-Agent": DEFAULT_HEADERS["User-Agent"],
                "Referer": "https://www.cninfo.com.cn/",
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        for s in data.get("stockList", []):
            code = s.get("code", "").strip()
            if code:
                _stock_cache[code] = {
                    "code": code,
                    "orgId": s.get("orgId", ""),
                    "name": s.get("zwjc", ""),
                }

        log.info("已加载 %d 只A股", len(_stock_cache))

    except Exception as e:
        log.warning("股票列表加载失败: %s", e)

    return _stock_cache


def _build_stock_param(code: str) -> str:
    """Build the stock parameter value: 'code,orgId' format required by cninfo API."""
    info = _load_stock_list().get(code.strip())
    if info and info.get("orgId"):
        return f"{info['code']},{info['orgId']}"
    return code


# ---------------------------------------------------------------------------
# API: Fetch announcements
# ---------------------------------------------------------------------------

def fetch_annual_reports(stock: str, date_from: str, date_to: str) -> list[dict]:
    """Fetch annual report announcements from cninfo (server-side category filter)."""
    se_date = f"{date_from}~{date_to}"
    all_announcements = []
    page_num = 1

    while True:
        payload = {
            "pageNum": str(page_num),
            "pageSize": "30",
            "column": "szse",
            "tabName": "fulltext",
            "plate": "",
            "stock": _build_stock_param(stock),
            "searchkey": "",
            "secid": "",
            "category": ANNUAL_REPORT_CATEGORY,
            "trade": "",
            "seDate": se_date,
            "sortName": "",
            "sortType": "",
            "isHLtitle": "true",
        }

        log.info("请求第 %d 页...", page_num)

        try:
            resp = requests.post(
                CNINFO_SEARCH_URL,
                data=payload,
                headers=DEFAULT_HEADERS,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            log.error("请求失败: %s", e)
            break

        announcements = data.get("announcements") or []
        if not announcements:
            if page_num == 1:
                log.info("未找到公告")
            break

        total_pages = data.get("totalpages", 1)
        if page_num == 1:
            log.info("共 %d 条公告，%d 页", data.get("totalRecordNum", 0), total_pages)

        for ann in announcements:
            title = re.sub(r"</?em>", "", ann.get("announcementTitle", "")).strip()
            ann_time_ms = ann.get("announcementTime", 0)

            ann_date = ""
            if ann_time_ms:
                try:
                    ann_date = datetime.fromtimestamp(ann_time_ms / 1000).strftime("%Y-%m-%d")
                except (OverflowError, OSError, TypeError, ValueError) as e:
                    log.debug("公告时间戳解析失败（%s）: %r", e, ann_time_ms)

            adjunct_url = ann.get("adjunctUrl", "")
            all_announcements.append({
                "secCode": ann.get("secCode", "").strip(),
                "secName": re.sub(r"</?em>", "", ann.get("secName", "")).strip(),
                "title": title,
                "announcementDate": ann_date,
                "adjunctUrl": adjunct_url,
                "adjunctSize": ann.get("adjunctSize", 0),
                "adjunctType": ann.get("adjunctType", ""),
                "pdfUrl": f"{CNINFO_PDF_BASE}{adjunct_url}" if adjunct_url else "",
            })

        if page_num >= total_pages:
            break

        page_num += 1
        time.sleep(REQUEST_INTERVAL)

    return all_announcements


def parse_date(s: str) -> datetime | None:
    """解析 YYYY-MM-DD 日期。"""
    return datetime.strptime(s, "%Y-%m-%d") if s else None


def search_filings(stock_code: str, date_from: datetime | None = None,
                   date_to: datetime | None = None) -> list[dict]:
    """搜索年报元数据（服务端按年报分类过滤，再按标题排除摘要/英文版）。"""
    today = datetime.now()
    if date_to is None:
        date_to = today
    if date_from is None:
        date_from = today - timedelta(days=5 * 365)

    log.info("开始搜索: 股票=%s, 区间 %s ~ %s",
             stock_code, date_from.strftime("%Y-%m-%d"), date_to.strftime("%Y-%m-%d"))

    announcements = fetch_annual_reports(
        stock=stock_code,
        date_from=date_from.strftime("%Y-%m-%d"),
        date_to=date_to.strftime("%Y-%m-%d"),
    )
    log.info("搜索完成，累计命中 %d 条", len(announcements))

    pdf_only = [r for r in announcements if r.get("adjunctType", "").upper() == "PDF"]
    if len(pdf_only) != len(announcements):
        log.info("文件类型过滤: 滤除 %d 条非 PDF 结果", len(announcements) - len(pdf_only))

    kept = [r for r in pdf_only if is_annual_report(r["title"])]
    if len(kept) != len(pdf_only):
        log.info("年报正文过滤: %d 条中保留 %d 条（滤除摘要/英文版等）", len(pdf_only), len(kept))
    return kept


def atomic_write(path: Path, data: bytes):
    """先写临时文件再改名，避免进程中断留下半截文件。"""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def upsert_index(company_dir: Path, market: str, code: str, name: str, entry: dict):
    """向公司 index.json 插入/更新一条公告条目，保持 filings 按日期升序。

    增量安全：合并而非替换已有条目，保留本地额外字段（如手工批注）；原子写防损坏。
    """
    idx_path = company_dir / "index.json"
    if idx_path.exists():
        index = json.loads(idx_path.read_text(encoding="utf-8"))
    else:
        index = {"market": market, "stockCode": code, "stockName": name, "filings": []}
    filings = []
    for r in index["filings"]:
        if not isinstance(r, dict):
            continue  # 跳过脏数据
        if r["file"] == entry["file"]:
            entry = {**r, **entry}  # 新数据覆盖同名字段，本地额外字段保留
            continue
        filings.append(r)
    filings.append(entry)
    filings.sort(key=lambda r: r["announcementDate"] if r.get("announcementDate") else "")
    index["filings"] = filings
    atomic_write(idx_path, (json.dumps(index, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def _cleanup_orphans(company_dir: Path, expected_filenames: set[str]) -> int:
    """删除本地多余 PDF 文件并更新 index.json，返回删除数量。"""
    idx_path = company_dir / "index.json"
    index = json.loads(idx_path.read_text(encoding="utf-8")) if idx_path.exists() else None
    removed = 0
    for f in list(company_dir.iterdir()):
        if f.is_dir() or f.name == "index.json":
            continue
        if f.name not in expected_filenames:
            f.unlink()
            removed += 1
            log.info("  删除多余文件: %s", f.name)
    if index and removed:
        before = len(index.get("filings", []))
        index["filings"] = [r for r in index["filings"] if r.get("file") in expected_filenames]
        after = len(index["filings"])
        if after < before:
            atomic_write(idx_path, (json.dumps(index, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
            log.info("  index.json 已清理 %d 条旧条目", before - after)
    return removed


def download_filings(filings: list[dict], root: Path, market: str = "cn", force: bool = False):
    """按 lakehouse 规范（仓库 lakehouse/README.md）串行下载年报 PDF 并维护 index.json。

    增量安全（默认）：不覆盖已有 PDF、不删本地条目、合并保留本地额外字段；
    幂等：按原文件名去重，已存在的文件跳过下载但仍补登 index 条目；
    原子写：PDF 与 index.json 均先写临时文件再改名，中断不留半截文件。

    --force 模式：覆盖已有 PDF 并清理本地多余文件，使本地目录与搜索结果精确同步。
    """
    sess = requests.Session()
    ok = skip = fail = 0

    # 预计算文件名集合，用于 force 模式下的清理
    expected_filenames: set[str] = set()
    for f in filings:
        if f.get("pdfUrl"):
            orig_name = f.get("adjunctUrl", "").split("/")[-1] or "download.pdf"
            expected_filenames.add(f"{f.get('announcementDate', 'nodate')}_{orig_name}")

    for i, f in enumerate(filings, 1):
        pdf_url = f.get("pdfUrl")
        if not pdf_url:
            log.warning("[SKIP] 无链接: %s", f.get("title", ""))
            fail += 1
            continue

        sec_code = f.get("secCode", "unknown")
        company_dir = root / market / sec_code
        orig_name = f.get("adjunctUrl", "").split("/")[-1] or "download.pdf"
        ann_date = f.get("announcementDate", "nodate")
        filename = f"{ann_date}_{orig_name}"
        path = company_dir / filename

        entry = {
            "announcementDate": ann_date,
            "title": f["title"],
            "file": filename,
            "link": pdf_url,
            "fileType": f.get("adjunctType", "PDF"),
            "fileSize": f.get("adjunctSize", 0),
            "secCode": sec_code,
            "secName": f.get("secName", ""),
        }

        try:
            if path.exists() and not force:
                log.info("[SKIP] 已存在: %s", path)
                skip += 1
            else:
                action = "覆盖下载" if force and path.exists() else "下载"
                log.info("%s第 %d/%d 个: %s", action, i, len(filings), f["title"])
                company_dir.mkdir(parents=True, exist_ok=True)

                resp = sess.get(
                    pdf_url,
                    headers={
                        "User-Agent": DEFAULT_HEADERS["User-Agent"],
                        "Referer": "https://www.cninfo.com.cn/",
                    },
                    timeout=60,
                )
                resp.raise_for_status()

                content_type = resp.headers.get("Content-Type", "")
                if "pdf" not in content_type.lower() and not resp.content[:5].startswith(b"%PDF"):
                    log.error("[FAIL] 不是 PDF: %s (ct=%s)", filename, content_type[:30])
                    fail += 1
                    continue

                atomic_write(path, resp.content)
                log.info("[OK] %s", path)
                ok += 1
                time.sleep(REQUEST_INTERVAL)

            upsert_index(company_dir, market, sec_code, f.get("secName", ""), entry)
        except Exception as e:
            log.error("[FAIL] %s: %s", pdf_url, e)
            fail += 1

    # Force 模式：清理本地多余文件
    if force and filings:
        sec_code = filings[0].get("secCode", "unknown")
        company_dir = root / market / sec_code
        removed = _cleanup_orphans(company_dir, expected_filenames)
        if removed:
            log.info("已清理 %d 个多余 PDF 文件", removed)

    log.info("下载完成: %d 新下载, %d 已存在跳过, %d 失败", ok, skip, fail)


def download_stock(stock_code: str, output: Path, date_from: str = "", date_to: str = "",
                   force: bool = False) -> None:
    """搜索并下载单只股票的年报到 lakehouse（函数调用入口，供批量脚本使用）。

    日期参数为 YYYY-MM-DD 字符串，空串表示用内置默认（近5年起、至今）。
    """
    filings = search_filings(
        stock_code=stock_code,
        date_from=parse_date(date_from),
        date_to=parse_date(date_to),
    )
    log.info("共找到 %d 条年报", len(filings))
    download_filings(filings, output, force=force)

