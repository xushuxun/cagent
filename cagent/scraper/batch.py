"""公告批量下载编排：按市场与股票名单逐个调用本包的单只爬虫。

幂等断点续跑：已存在的 PDF 自动跳过，进程中断后重跑同一命令即可继续。
单只失败只记日志、不中断批次；结束输出失败清单并以退出码 1 结束（编排入口在
cagent/scraper/cli.py）。
"""

import logging
from pathlib import Path

from cagent.scraper.cninfo_scraper import download_stock as cn_download_stock
from cagent.scraper.hkex_scraper import download_stock as hk_download_stock

log = logging.getLogger("cagent.scraper")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LAKEHOUSE_DIR = PROJECT_ROOT / "lakehouse"

# market -> 单只下载函数（--list 模式下按每条的 market 字段分发）
SCRAPERS = {
    "cn": cn_download_stock,
    "hk": hk_download_stock,
}

# market -> 标的名单文件
STOCK_LISTS = {
    "cn": LAKEHOUSE_DIR / "cn_stocks.json",
    "hk": LAKEHOUSE_DIR / "hk_stock_connect.json",
}


def download_stocks(market: str, stocks: list[dict],
                    date_from: str, date_to: str,
                    output: Path, offset: int, limit: int, force: bool) -> list[str]:
    """用指定爬虫下载一个市场的一组标的年报，返回失败股票代码列表。"""
    total_all = len(stocks)
    stocks = stocks[offset:offset + limit if limit > 0 else None]
    if not stocks:
        log.warning("[%s] 无标的可处理（offset=%d, limit=%d，名单共 %d 只）",
                    market, offset, limit, total_all)
        return []

    log.info("=== 市场 %s: 处理第 %d~%d 只 / 名单共 %d 只 ===",
             market, offset + 1, offset + len(stocks), total_all)
    failed = []
    for i, s in enumerate(stocks, 1):
        code, name = s["code"], s.get("name", "")
        log.info("[%s %d/%d] %s %s", market, i, len(stocks), code, name)
        try:
            SCRAPERS[market](code, output, date_from, date_to, force=force)
        except Exception as exc:
            log.error("  [FAIL] %s %s（%s）", code, name, exc)
            failed.append(code)

    log.info("=== 市场 %s 完成: %d 只处理, %d 只失败 ===", market, len(stocks), len(failed))
    return failed


def group_by_market(stocks: list[dict]) -> dict[str, list[dict]]:
    """按 market 字段分组（保持 cn 在前、hk 在后，组内维持原顺序）。"""
    groups: dict[str, list[dict]] = {}
    for s in stocks:
        m = s.get("market", "")
        if m not in SCRAPERS:
            raise ValueError(f"标的 {s.get('code')} 的 market 字段无效: {m!r}（应为 cn/hk）")
        groups.setdefault(m, []).append(s)
    return {m: groups[m] for m in ("cn", "hk") if m in groups}