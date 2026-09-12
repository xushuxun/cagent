"""OCR 批处理编排：单家公司 / 单市场 / 全市场，返回失败股票代码列表。

单家失败只记日志、不中断批次；待解析队列由队列模块计算，中断后重跑同一命令续跑。
文档之间串行，避免同时抢占显存与请求配额。
"""

import logging
from pathlib import Path

from cagent.ocr.convert import process_pdf
from cagent.ocr.pending import latest_pdf, pending_pdfs

log = logging.getLogger("ocr.batch")


def run(company_dir: Path, queue: list[Path]) -> int:
    """串行解析待 OCR 队列（单文档内页面并发）。返回失败份数。"""
    ok = fail = 0
    for i, pdf_path in enumerate(queue, 1):
        log.info("解析第 %d/%d 份: %s", i, len(queue), pdf_path.name)
        try:
            if process_pdf(pdf_path, company_dir):
                ok += 1
            else:
                fail += 1
        except Exception as exc:
            log.error("[FAIL] %s: %s", pdf_path, exc)
            fail += 1
    log.info("解析完成: %d 成功, %d 失败", ok, fail)
    return fail


def ocr_company(stock: str, market: str, lakehouse: Path,
                force: bool = False, pdf_limit: int = 0, latest: bool = False) -> int:
    """解析单家公司待 OCR 的公告，返回失败份数（0 = 无待解析或全部成功）。

    CLI 与 pipeline 共用的函数入口，语义与命令行参数一致。
    """
    company_dir = lakehouse / market / stock
    if latest:
        queue = latest_pdf(company_dir, force=force)
    else:
        queue = pending_pdfs(company_dir, force=force)
        if pdf_limit > 0:
            queue = queue[:pdf_limit]
    if not queue:
        log.info("没有待解析的 PDF（derived/ 已是最新）")
        return 0
    log.info("待解析 %d 份 PDF", len(queue))
    return run(company_dir, queue)


def ocr_stock(stock: str, markets: list[str], lakehouse: Path, force: bool,
              pdf_limit: int, latest: bool) -> list[str]:
    """只解析一家公司（在给定市场里找它的目录），返回失败股票代码列表。"""
    failed: list[str] = []
    found = False
    for market in markets:
        if not (lakehouse / market / stock / "index.json").exists():
            continue
        found = True
        log.info("=== %s %s ===", market, stock)
        try:
            fail = ocr_company(stock=stock, market=market, lakehouse=lakehouse,
                               force=force, pdf_limit=pdf_limit, latest=latest)
        except Exception as exc:
            log.error("[FAIL] %s %s（%s）", market, stock, exc)
            fail = 1
        if fail:
            failed.append(stock)
    if not found:
        log.error("找不到 %s 的公告目录（找过 %s）", stock, "、".join(markets))
        return [stock]
    return failed


def ocr_market(market: str, lakehouse: Path, offset: int, limit: int, force: bool,
               pdf_limit: int, latest: bool) -> list[str]:
    """遍历一个市场下的公司目录逐个解析，返回失败股票代码列表。"""
    market_dir = lakehouse / market
    if not market_dir.is_dir():
        log.warning("[%s] 目录不存在: %s（先跑批量下载：uv run python cagent/scraper/cli.py）", market, market_dir)
        return []
    companies = sorted(p.name for p in market_dir.iterdir()
                       if p.is_dir() and (p / "index.json").exists())
    total = len(companies)
    companies = companies[offset:offset + limit if limit > 0 else None]
    if not companies:
        log.warning("[%s] 无公司可处理（offset=%d, limit=%d，共 %d 家）",
                    market, offset, limit, total)
        return []
    log.info("=== 市场 %s: 处理第 %d~%d 家 / 共 %d 家 ===",
             market, offset + 1, offset + len(companies), total)
    failed: list[str] = []
    for i, code in enumerate(companies, 1):
        log.info("[%s %d/%d] %s", market, i, len(companies), code)
        try:
            fail = ocr_company(stock=code, market=market, lakehouse=lakehouse,
                               force=force, pdf_limit=pdf_limit, latest=latest)
        except Exception as exc:
            log.error("  [FAIL] %s（%s）", code, exc)
            failed.append(code)
            continue
        if fail:
            log.error("  [FAIL] %s（%d 份解析失败）", code, fail)
            failed.append(code)
    log.info("=== 市场 %s 完成: %d 家处理, %d 家失败 ===", market, len(companies), len(failed))
    return failed