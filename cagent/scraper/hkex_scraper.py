r"""HKEx Annual Report Scraper — 港交所披露易年报检索与下载（库模块）。

直接调用港交所未公开 JSON API（按年报分类服务端过滤），无需浏览器。
进度日志输出到 stderr。CLI 入口在本包 cli.py
（`uv run python cagent/scraper/cli.py --stock <code> --market hk`），本模块只提供
函数：search_filings（检索）/ download_stock（单只下载）。
"""

import json
import logging
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stderr,
)
log: logging.Logger = logging.getLogger("hkex")

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
    if any(k in t for k in ("摘要", "英文", "English", "Abridged")) \
            or any(k in tl for k in ("english", "abridged")):
        return False
    return (any(k in t for k in ("年報", "年报", "年度報告", "年度报告"))
            or "annual report" in tl)


def norm_stock(code: str) -> str:
    """统一股票代码为5位数字字符串，用于比对。"""
    return code.lstrip("0").zfill(5) if code else ""


def parse_record(rec: dict) -> dict:
    """把 HKEx API 原始记录转成统一字段。"""
    link = rec.get("FILE_LINK", "")
    if link.startswith("/"):
        link = BASE + link
    return {
        "stockCode": rec.get("STOCK_CODE", "").split("<br/>")[0].strip(),
        "stockName": clean(rec.get("STOCK_NAME", "").split("<br/>")[0]),
        "title": clean(rec.get("TITLE", "")
                       .replace("&#x3b;", ";").replace("&amp;", "&")
                       .replace("&#x2f;", "/").replace("&#x2F;", "/")),
        "date": (rec.get("DATE_TIME", "").split(" ") or [""])[0],
        "link": link,
        "fileType": (rec.get("FILE_TYPE", "") or "").upper(),
        "fileSize": (rec.get("FILE_INFO", "") or "").strip(),
    }


def parse_date(s: str) -> datetime | None:
    """解析 YYYY-MM-DD 日期。"""
    return datetime.strptime(s, "%Y-%m-%d") if s else None


def resolve_stock_id(sess: requests.Session, code: str) -> str:
    """通过 prefix.do 自动补全接口解析股票代码对应的 stockId；失败返回空串。"""
    resp = sess.get(f"{BASE}/search/prefix.do", params={
        "callback": "callback", "lang": "ZH", "type": "A", "name": code, "market": "SEHK",
    }, headers={"Referer": SEARCH}, timeout=30)
    resp.raise_for_status()
    text = resp.text
    payload = text[text.index("(") + 1:text.rindex(")")]
    info = json.loads(payload).get("stockInfo") or []
    target = norm_stock(code)
    for item in info:
        if norm_stock(str(item.get("code", ""))) == target:
            return str(item.get("stockId", ""))
    return ""


def fetch_filings(sess: requests.Session, frm: str, to: str, stock_id: str) -> list[dict]:
    """抓取指定日期区间的年报；stockId + 分类代码在服务端精确过滤，直接调 JSON API，无需 JSF 会话。"""
    log.info("抓取区间 %s ~ %s", frm, to)
    out, fetched, total = [], 0, None
    while True:
        size = 5000
        resp = sess.get(API, params={
            "sortDir": "0", "sortByOptions": "DateTime", "category": "0", "market": "SEHK",
            "stockId": stock_id, "documentType": "-1", "fromDate": frm, "toDate": to,
            "title": "", "searchType": "1",
            # 服务端分类过滤：t1code=40000（財務報表/ESG 類），t2code=40100（年報）
            # 服务端不过滤：t1code=-2，t2code=-2
            "t1code": "40000", "t2Gcode": "-2", "t2code": "40100",
            "rowRange": str(fetched + size), "lang": "ZH",
        }, headers={"Referer": SEARCH, "X-Requested-With": "XMLHttpRequest"}, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        raw = data.get("result") or "null"
        if raw == "null" or not raw:
            break
        rows = json.loads(raw)
        if rows and total is None:
            total = int(rows[0].get("TOTAL_COUNT", "0"))
        if len(rows) <= fetched:
            # API 返回行数没有增长（服务端单次查询有行数上限），避免死循环
            log.warning("分页无进展（已取 %s/%s 条），超出部分被服务端截断，提前结束", fetched, total or "?")
            break
        for r in rows[fetched:]:
            out.append(parse_record(r))
        fetched = len(rows)
        log.info("  分页进度: %s/%s 条，已命中 %s 条", fetched, total or "?", len(out))
        if not data.get("hasNextRow") or (total and fetched >= total):
            break
    log.info("区间 %s ~ %s 完成，共 %s 条命中", frm, to, len(out))
    return out


def search_filings(stock_code: str, date_from: datetime | None = None,
                   date_to: datetime | None = None) -> list[dict]:
    """搜索年报元数据（stockId + 年报分类代码均由服务端过滤）。"""
    today = datetime.now()
    if date_to is None:
        date_to = today
    if date_from is None:
        # 默认从5年前查起
        date_from = today - timedelta(days=5 * 365)
    if date_from < HKEXNEWS_LAUNCH:
        log.warning("起始日期 %s 早于披露易上线日，已截断为 2007-06-25", date_from.strftime("%Y-%m-%d"))
        date_from = HKEXNEWS_LAUNCH

    log.info("开始搜索: 股票=%s, 区间 %s ~ %s",
             stock_code, date_from.strftime("%Y-%m-%d"), date_to.strftime("%Y-%m-%d"))
    sess = requests.Session()
    sess.headers.update({"User-Agent": USER_AGENT})

    stock_id = resolve_stock_id(sess, stock_code)
    if not stock_id:
        raise ValueError(f"未能解析股票代码 {stock_code} 的 stockId，请确认代码是否正确")
    log.info("已解析 stockId: %s -> %s（服务端精确过滤）", stock_code, stock_id)

    # stockId 精确查询不限制日期跨度，一次请求即可拿全部历史
    result = fetch_filings(sess, date_from.strftime("%Y%m%d"), date_to.strftime("%Y%m%d"), stock_id)
    log.info("搜索完成，累计命中 %d 条", len(result))

    pdf_only = [r for r in result if r["fileType"] == "PDF"]
    if len(pdf_only) != len(result):
        log.info("文件类型过滤: 滤除 %d 条非 PDF 结果", len(result) - len(pdf_only))

    kept = [r for r in pdf_only if is_annual_report(r["title"])]
    if len(kept) != len(pdf_only):
        log.info("年报正文过滤: %d 条中保留 %d 条（滤除通知信函/ESG/可持续报告等）",
                 len(pdf_only), len(kept))
    return kept


def iso_date(ddmmyyyy: str) -> str:
    """HKEx 的 DD/MM/YYYY 日期转 ISO YYYY-MM-DD。"""
    return datetime.strptime(ddmmyyyy, "%d/%m/%Y").strftime("%Y-%m-%d")


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
    filings.sort(key=lambda r: r["date"])
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


def download_filings(filings: list[dict], root: Path, market: str = "hk", force: bool = False):
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
        if f.get("link"):
            orig_name = f["link"].split("/")[-1].split("?")[0] or "download"
            expected_filenames.add(f"{iso_date(f['date'])}_{orig_name}")

    for i, f in enumerate(filings, 1):
        if not f.get("link"):
            log.warning("[SKIP] 无链接: %s", f.get("title", ""))
            fail += 1
            continue
        code = norm_stock(f["stockCode"])
        company_dir = root / market / code
        orig_name = f["link"].split("/")[-1].split("?")[0] or "download"
        filename = f"{iso_date(f['date'])}_{orig_name}"
        path = company_dir / filename
        entry = {
            "date": iso_date(f["date"]),
            "title": f["title"],
            "file": filename,
            "link": f["link"],
            "fileType": f["fileType"],
            "fileSize": f["fileSize"],
        }
        try:
            if path.exists() and not force:
                log.info("[SKIP] 已存在: %s", path)
                skip += 1
            else:
                action = "覆盖下载" if force and path.exists() else "下载"
                log.info("%s第 %d/%d 个: %s", action, i, len(filings), f["title"])
                company_dir.mkdir(parents=True, exist_ok=True)
                resp = sess.get(f["link"], headers={"User-Agent": USER_AGENT}, timeout=120)
                resp.raise_for_status()
                atomic_write(path, resp.content)
                log.info("[OK] %s", path)
                ok += 1
            upsert_index(company_dir, market, code, f["stockName"], entry)
        except Exception as e:
            log.error("[FAIL] %s: %s", f["link"], e)
            fail += 1

    # Force 模式：清理本地多余文件
    if force and filings:
        code = norm_stock(filings[0]["stockCode"])
        company_dir = root / market / code
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

