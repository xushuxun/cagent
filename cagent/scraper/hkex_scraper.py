r"""HKEx Annual Report Scraper — 港交所披露易年报检索与下载（库模块）。

直接调用港交所未公开 JSON API（按年报分类服务端过滤），无需浏览器。
进度日志输出到 stderr。CLI 入口在本包 cli.py
（`uv run cagent/scraper/cli.py --stock <code> --market hk`），本模块只提供
函数：search_filings（检索）/ download_stock（单只下载）。
"""

import json
import logging
import re
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import requests

from cagent.scraper.lakehouse_index import (
    atomic_write,
    cleanup_orphans,
    parse_date,
    upsert_index,
)

log = logging.getLogger("hkex")

# 港交所披露易站点常量
BASE = "https://www1.hkexnews.hk"
SEARCH = f"{BASE}/search/titlesearch.xhtml"
API = f"{BASE}/search/titleSearchServlet.do"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

# 披露易网站 2007-06-25 上线，此前的公告不在电子披露系统内，查不到
HKEXNEWS_LAUNCH = datetime(2007, 6, 25)


def clean(s: str) -> str:
    """移除不可打印字符，合并连续空白。"""
    s = "".join(c if c.isprintable() or c == " " else " " for c in str(s))
    return re.sub(r"\s+", " ", s).strip()


def is_annual_report(title: str) -> bool:
    """HKEx 的“年报”分类会夹带通知信函、ESG/可持续报告等，只保留年报正文。

    判定标准只认标题里的年报词（年報/年报/年度報告/年度报告/Annual Report）；
    ESG、可持续、通知信函等标题不包含这些词，自然被滤除。空标题保守保留。
    """
    t = clean(title)
    tl = t.lower()
    if not t:
        return True
    if "english" in tl or "abridged" in tl:  # 英文版、节录版，不要
        return False
    return any(k in t for k in ("年報", "年报", "年度報告", "年度报告")) or "annual report" in tl


def norm_stock(code: str) -> str:
    """统一股票代码为5位数字字符串，用于比对。"""
    return code.lstrip("0").zfill(5) if code else ""


def iso_date(ddmmyyyy: str) -> str:
    """HKEx 的 DD/MM/YYYY 日期转 ISO YYYY-MM-DD。"""
    return datetime.strptime(ddmmyyyy, "%d/%m/%Y").strftime("%Y-%m-%d")


def parse_record(rec: dict) -> dict:
    """把 HKEx API 原始记录转成统一字段。"""
    link = rec.get("FILE_LINK", "")
    return {
        "stockCode": rec.get("STOCK_CODE", "").split("<br/>")[0].strip(),
        "stockName": clean(rec.get("STOCK_NAME", "").split("<br/>")[0]),
        "title": clean(rec.get("TITLE", "").replace("&#x3b;", ";").replace("&amp;", "&").replace("&#x2f;", "/").replace("&#x2F;", "/")),
        "date": rec.get("DATE_TIME", "").split(" ")[0],
        "link": BASE + link if link.startswith("/") else link,
        "fileType": (rec.get("FILE_TYPE", "") or "").upper(),
        "fileSize": (rec.get("FILE_INFO", "") or "").strip(),
    }


def resolve_stock_id(sess: requests.Session, code: str) -> str:
    """通过 prefix.do 自动补全接口解析股票代码对应的 stockId；失败返回空串。

    响应是 JSONP（callback(...)）包装，这里剥掉外壳取 JSON；接口改版会直接 ValueError。
    """
    resp = sess.get(
        f"{BASE}/search/prefix.do",
        params={
            "callback": "callback",
            "lang": "ZH",
            "type": "A",
            "name": code,
            "market": "SEHK",
        },
        headers={"Referer": SEARCH},
        timeout=30,
    )
    resp.raise_for_status()
    text = resp.text
    info = json.loads(text[text.index("(") + 1 : text.rindex(")")]).get("stockInfo") or []
    target = norm_stock(code)
    return next(
        (str(item.get("stockId", "")) for item in info if norm_stock(str(item.get("code", ""))) == target),
        "",
    )


def _fetch_rows(sess: requests.Session, frm: str, to: str, stock_id: str, row_range: int) -> dict:
    resp = sess.get(
        API,
        params={
            "sortDir": "0",
            "sortByOptions": "DateTime",
            "category": "0",
            "market": "SEHK",
            "stockId": stock_id,
            "documentType": "-1",
            "fromDate": frm,
            "toDate": to,
            "title": "",
            "searchType": "1",
            # 服务端分类过滤：t1code=40000（財務報表/ESG 類），t2code=40100（年報）
            # 服务端不过滤：t1code=-2，t2code=-2
            "t1code": "40000",
            "t2Gcode": "-2",
            "t2code": "40100",
            "rowRange": str(row_range),
            "lang": "ZH",
        },
        headers={"Referer": SEARCH, "X-Requested-With": "XMLHttpRequest"},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_filings(sess: requests.Session, frm: str, to: str, stock_id: str) -> list[dict]:
    """抓取指定日期区间的年报；stockId + 分类代码在服务端精确过滤，直接调 JSON API，无需 JSF 会话。"""
    log.info(f"抓取区间 {frm} ~ {to}")
    size = 5000  # 单次请求行数上限；服务端实际返回行数可能更少，靠“行数不再增长”判终止
    out, fetched, total = [], 0, None
    while True:
        data = _fetch_rows(sess, frm, to, stock_id, fetched + size)
        raw = data.get("result") or "null"
        if raw == "null" or not raw:
            break
        rows = json.loads(raw)
        if rows and total is None:
            total = int(rows[0].get("TOTAL_COUNT", "0"))
        if len(rows) <= fetched:
            # API 返回行数没有增长（服务端单次查询有行数上限），避免死循环
            log.warning(f"分页无进展（已取 {fetched}/{total or '?'} 条），超出部分被服务端截断，提前结束")
            break
        out += [parse_record(r) for r in rows[fetched:]]
        fetched = len(rows)
        log.info(f"  分页进度: {fetched}/{total or '?'} 条，已命中 {len(out)} 条")
        if not data.get("hasNextRow") or (total and fetched >= total):
            break
    log.info(f"区间 {frm} ~ {to} 完成，共 {len(out)} 条命中")
    return out


def search_filings(stock_code: str, date_from: datetime | None = None, date_to: datetime | None = None) -> list[dict]:
    """搜索年报元数据（stockId + 年报分类代码均由服务端过滤）。"""
    today = datetime.now()
    date_from, date_to = date_from or today - timedelta(days=5 * 365), date_to or today
    if date_from < HKEXNEWS_LAUNCH:
        log.warning(f"起始日期 {date_from:%Y-%m-%d} 早于披露易上线日，已截断为 2007-06-25")
        date_from = HKEXNEWS_LAUNCH
    log.info(f"开始搜索: 股票={stock_code}, 区间 {date_from:%Y-%m-%d} ~ {date_to:%Y-%m-%d}")

    sess = requests.Session()
    sess.headers.update({"User-Agent": USER_AGENT})
    stock_id = resolve_stock_id(sess, stock_code)
    if not stock_id:
        raise ValueError(f"未能解析股票代码 {stock_code} 的 stockId，请确认代码是否正确")
    log.info(f"已解析 stockId: {stock_code} -> {stock_id}（服务端精确过滤）")

    # stockId 精确查询不限制日期跨度，一次请求即可拿全部历史
    result = fetch_filings(sess, date_from.strftime("%Y%m%d"), date_to.strftime("%Y%m%d"), stock_id)
    kept = [r for r in result if r["fileType"] == "PDF" and is_annual_report(r["title"])]
    log.info(f"搜索完成：命中 {len(result)} 条，滤除非 PDF 与通知信函/ESG/可持续报告后保留 {len(kept)} 条")
    return kept


def _filename(f: dict) -> str:
    """公告文件名：ISO 日期前缀 + 链接末段原文件名（见 lakehouse/README.md 命名规则）。"""
    orig = f["link"].split("/")[-1].split("?")[0] or "download"
    return f"{iso_date(f['date'])}_{orig}"


def _download_one(
    sess: requests.Session,
    f: dict,
    root: Path,
    market: str,
    force: bool,
    i: int,
    total: int,
) -> str:
    """下载单条公告 PDF 并补登 index 条目，返回 ok/skip/fail。"""
    if not f.get("link"):
        log.warning(f"[SKIP] 无链接: {f.get('title', '')}")
        return "fail"
    code = norm_stock(f["stockCode"])
    company_dir = root / market / code
    path = company_dir / _filename(f)
    entry = {
        "date": iso_date(f["date"]),
        "title": f["title"],
        "file": path.name,
        "link": f["link"],
        "fileType": f["fileType"],
        "fileSize": f["fileSize"],
    }
    try:
        if path.exists() and not force:
            log.info(f"[SKIP] 已存在: {path}")
            result = "skip"
        else:
            log.info(f"{'覆盖下载' if force and path.exists() else '下载'}第 {i}/{total} 个: {f['title']}")
            company_dir.mkdir(parents=True, exist_ok=True)
            resp = sess.get(f["link"], headers={"User-Agent": USER_AGENT}, timeout=120)
            resp.raise_for_status()
            atomic_write(path, resp.content)
            log.info(f"[OK] {path}")
            result = "ok"
        upsert_index(company_dir, market, code, f["stockName"], entry)
        return result
    except Exception as e:
        log.error(f"[FAIL] {f['link']}: {e}")
        return "fail"


def download_filings(filings: list[dict], root: Path, market: str, force: bool = False) -> None:
    """按 lakehouse 规范（仓库 lakehouse/README.md）串行下载年报 PDF 并维护 index.json。

    增量安全（默认）：不覆盖已有 PDF、不删本地条目、合并保留本地额外字段；
    幂等：按原文件名去重，已存在的文件跳过下载但仍补登 index 条目；
    原子写：PDF 与 index.json 均先写临时文件再改名，中断不留半截文件。

    --force 模式：覆盖已有 PDF 并清理本地多余文件，使本地目录与搜索结果精确同步。
    """
    sess = requests.Session()
    stats = Counter(_download_one(sess, f, root, market, force, i, len(filings)) for i, f in enumerate(filings, 1))

    if force and filings:
        company_dir = root / market / norm_stock(filings[0]["stockCode"])
        expected = {_filename(f) for f in filings if f.get("link")}
        if removed := cleanup_orphans(company_dir, expected):
            log.info(f"已清理 {removed} 个多余 PDF 文件")

    log.info(f"下载完成: {stats['ok']} 新下载, {stats['skip']} 已存在跳过, {stats['fail']} 失败")


def download_stock(
    stock_code: str,
    output: Path,
    date_from: str = "",
    date_to: str = "",
    force: bool = False,
) -> None:
    """搜索并下载单只股票的年报到 lakehouse（函数调用入口，供批量脚本使用）。

    日期参数为 YYYY-MM-DD 字符串，空串表示用内置默认（近5年起、至今）。
    """
    filings = search_filings(stock_code, parse_date(date_from), parse_date(date_to))
    log.info(f"共找到 {len(filings)} 条年报")
    download_filings(filings, output, "hk", force=force)
