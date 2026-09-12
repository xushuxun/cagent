"""环节·下载验证（爬虫契约）：公告文件库的下载规则（cn 601633 与 hk 02333 同步覆盖）。

    uv run python cagent/tests/test_scraper.py                   # 离线：验本地数据目录一致性
    uv run python cagent/tests/test_scraper.py --live            # 加跑真实下载到临时目录

验收标准（对应 AGENTS.md 架构总览「下载」环节）：raw 层不改写；索引与文件一致；
raw 文件名以 ISO 日期前缀；目录内无 .tmp 残留。离线档零网络、零写；
--live 只在临时目录里下载（近 2 年窗口），绝不碰真实数据目录。
"""

import argparse
import hashlib
import json
import re
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from cagent.tests._util import report

PAIRS = [("cn", "601633"), ("hk", "02333")]  # 同一家公司 A/H 两地，端到端对比的标的
LIVE_WINDOW_YEARS = 2
_ISO_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_")


def _pdf_files(company_dir: Path) -> list[Path]:
    return sorted(p for p in company_dir.iterdir()
                  if p.is_file() and p.name != "index.json"
                  and p.suffix.lower() == ".pdf")


def _md5(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def _load_index(company_dir: Path) -> dict | None:
    idx = company_dir / "index.json"
    if not idx.exists():
        return None
    return json.loads(idx.read_text(encoding="utf-8"))


def offline_checks() -> list[tuple[str, bool, str]]:
    """零网络、零写：核对真实数据目录里两家公司的下载契约。"""
    from cagent.lib.lakehouse import LakehouseReader
    reader = LakehouseReader()
    out: list[tuple[str, bool, str]] = []
    for market, code in PAIRS:
        company_dir = reader.company_dir(market, code)
        index = _load_index(company_dir)
        if index is None:
            out.append((f"[{market}/{code}] index.json 存在", False, "缺 index.json"))
            continue
        names = [f.get("file", "") for f in index.get("filings", [])]
        raw_names = {p.name for p in _pdf_files(company_dir)}
        tmp_left = [p.name for p in company_dir.iterdir() if p.suffix == ".tmp"]
        out += [
            (f"[{market}/{code}] 索引非空", bool(names), f"{len(names)} 条公告"),
            (f"[{market}/{code}] 索引条目都在磁盘",
             all(n and (company_dir / n).exists() for n in names),
             f"{sum(1 for n in names if n and (company_dir / n).exists())}/{len(names)}"),
            (f"[{market}/{code}] raw 无孤儿文件",
             raw_names <= {n for n in names if n},
             f"孤儿 {sorted(raw_names - {n for n in names if n})}"),
            (f"[{market}/{code}] raw 无 .tmp 残留", not tmp_left, f"{tmp_left}"),
        ]
    return out


def live_checks() -> list[tuple[str, bool, str]]:
    """真实下载到临时目录（需要网络）：下载、命名、幂等重跑。"""
    from cagent.scraper.cninfo_scraper import download_stock as cn_download
    from cagent.scraper.hkex_scraper import download_stock as hk_download
    loaders = {"cn": cn_download, "hk": hk_download}
    start = (datetime.now().date() - timedelta(days=365 * LIVE_WINDOW_YEARS)).isoformat()
    today = datetime.now().date().isoformat()
    out: list[tuple[str, bool, str]] = []
    with tempfile.TemporaryDirectory(prefix="cagent-dl-") as tmp:
        root = Path(tmp)
        for market, code in PAIRS:
            company_dir = root / market / code
            try:
                loaders[market](code, root, date_from=start, date_to=today, force=False)
            except Exception as exc:
                out.append((f"[{market}/{code}] 真实下载执行", False,
                            f"{type(exc).__name__}: {exc}"))
                continue
            first = {p.name: _md5(p) for p in _pdf_files(company_dir)}
            index = _load_index(company_dir) or {}
            names = [f.get("file", "") for f in index.get("filings", [])]
            out += [
                (f"[{market}/{code}] 近{LIVE_WINDOW_YEARS}年下载产出",
                 bool(first) and all(n and (company_dir / n).exists() for n in names),
                 f"{len(first)} 个 PDF / {len(names)} 条索引"),
                (f"[{market}/{code}] raw 文件名带 ISO 日期前缀",
                 all(_ISO_PREFIX_RE.match(n) for n in first),
                 f"{sorted(first)[:3]}{'…' if len(first) > 3 else ''}"),
                (f"[{market}/{code}] 索引补齐",
                 bool(names) and {n for n in names if n} == set(first),
                 f"索引 {len(names)} 与磁盘 {len(first)} 一一对应"),
            ]
            try:  # 幂等：同参数重跑，不新增、不覆盖
                loaders[market](code, root, date_from=start, date_to=today, force=False)
            except Exception as exc:
                out.append((f"[{market}/{code}] 幂等重跑", False,
                            f"{type(exc).__name__}: {exc}"))
                continue
            second = {p.name: _md5(p) for p in _pdf_files(company_dir)}
            out.append((
                f"[{market}/{code}] 幂等：重跑不新增不覆盖",
                set(second) == set(first)
                and all(second[n] == first[n] for n in first),
                f"PDF {len(second)} 个，内容签名与原一致",
            ))
    return out


def main() -> None:
    p = argparse.ArgumentParser(
        description="环节·下载验证：cn 601633 与 hk 02333（默认离线，--live 真下载到临时目录）")
    p.add_argument("--live", action="store_true",
                   help=f"加跑真实下载（近 {LIVE_WINDOW_YEARS} 年窗口）到临时目录，需要网络")
    args = p.parse_args()

    checks = offline_checks()
    if args.live:
        checks += live_checks()
    print()
    report(checks)


if __name__ == "__main__":
    main()