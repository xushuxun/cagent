"""环节·解析验证：跑完整 OCR 流水线，从 lakehouse 产物判断（不测内部函数）。

    uv run python cagent/tests/test_ocr.py                # 离线：审计真实数据目录的 derived 产物
    uv run python cagent/tests/test_ocr.py --live         # 起 vllm，两地最新年报前几页完整解析
                                                          # 进临时 lakehouse，再从产物断言

验收标准（AGENTS.md「解析」环节）：页码连续无缺号且每页恰一次；失败页占位可被检出；
grounding 占位符在产物中零残留；无 .tmp 残留。live 档用真实 OCR 服务（with_vllm 自管），
走生产入口把 PDF 解析进临时 lakehouse 后再审计——不测 _parse_boxes 之类的内部函数。
"""

import json
import tempfile
from pathlib import Path

from cagent.lib.lakehouse import (
    PAGE_RE,
    LakehouseReader,
    page_failure_text,
    page_failures,
)
from cagent.ocr.pending import latest_pdf, pending_pdfs
from cagent.tests._util import report, with_vllm

PAIRS = [("cn", "601633"), ("hk", "02333")]  # 同一家公司 A/H 两地
FRONT_PAGES = 3  # live 档每家裁出前几页做完整解析（控制时长）


def audit_derived(company_dir: Path, prefix: str) -> list[tuple[str, bool, str]]:
    """从 lakehouse 产物判断：页码连续每页恰一次、grounding 零残留、失败页可检出、无 tmp。"""
    derived = company_dir / "derived"
    mds = sorted(derived.glob("*.md")) if derived.is_dir() else []
    if not mds:
        return [(f"{prefix} derived 产物存在", False,
                 f"{derived} 无 .md（先跑 --live 解析或重建解析层）")]
    out: list[tuple[str, bool, str]] = []
    for md in mds:
        text = md.read_text(encoding="utf-8", errors="replace")
        nums = [int(n) for n in PAGE_RE.findall(text)]
        pages = sorted(set(nums))
        consecutive = pages == list(range(1, len(pages) + 1))
        once_each = len(nums) == len(pages)
        leak = "<|ref|>" in text or "<|det|>" in text
        fails = page_failures(md)
        out += [
            (f"{prefix} {md.name}: 页码 1..N 连续", bool(pages) and consecutive,
             f"{len(pages)} 页"),
            (f"{prefix} {md.name}: 每页恰一次", once_each,
             f"重复页 {sorted({n for n in nums if nums.count(n) > 1})}"),
            (f"{prefix} {md.name}: grounding 零残留", not leak, ""),
            (f"{prefix} {md.name}: 失败页占位可检出",
             all(page_failure_text(n) in text for n in fails), f"失败页 {fails}"),
        ]
    tmp_left = [p for p in derived.rglob("*.tmp")]
    out.append((f"{prefix} 无 .tmp 残留", not tmp_left, f"{tmp_left}"))
    return out


def _sample_company(root: Path, market: str, code: str,
                    front_pages: int) -> tuple[Path, Path | None]:
    """把真实最新年报裁出前 N 页，放入临时公司目录并写 index.json；返回 (公司目录, PDF)。"""
    import pypdfium2 as pdfium
    reader = LakehouseReader()
    src_dir = reader.company_dir(market, code)
    src_pdf = latest_pdf(src_dir, force=True)
    if not src_pdf:
        return root / market / code, None
    company = root / market / code
    company.mkdir(parents=True, exist_ok=True)
    sample = company / "sample-annual.pdf"
    with pdfium.PdfDocument(str(src_pdf[0])) as doc:
        out = pdfium.PdfDocument.new()
        try:
            out.import_pages(doc, pages=list(range(min(front_pages, len(doc)))))
            out.save(str(sample))
        finally:
            out.close()
    title = "2025年年度报告" if market == "cn" else "2025年報"
    (company / "index.json").write_text(json.dumps(
        {"filings": [{"title": title, "file": sample.name}]},
        ensure_ascii=False), encoding="utf-8")
    return company, sample


def live_checks(pairs: list[tuple[str, str]] = PAIRS) -> list[tuple[str, bool, str]]:
    """--live：vllm 服务（with_vllm 自管）下，生产入口完整解析，产物审计。"""
    from cagent.ocr.batch import ocr_company
    out: list[tuple[str, bool, str]] = []
    with tempfile.TemporaryDirectory(prefix="ocr-live-") as tmp:
        root = Path(tmp)
        for market, code in pairs:
            company, pdf = _sample_company(root, market, code, FRONT_PAGES)
            prefix = f"[{market}/{code}]"
            if pdf is None or not pdf.exists():
                out.append((f"{prefix} 样本 PDF", False, "最新年报缺失（先跑下载环节）"))
                continue
            try:
                fail = ocr_company(stock=code, market=market, lakehouse=root, pdf_limit=1)
            except Exception as exc:
                out.append((f"{prefix} 完整流水线", False,
                            f"{type(exc).__name__}: {exc}"))
                continue
            out.append((f"{prefix} 完整流水线失败数", fail == 0, f"fail={fail}"))
            out += audit_derived(company, prefix)
            out.append((f"{prefix} 幂等：重跑无待解析",
                        pending_pdfs(company) == [],
                        f"{[p.name for p in pending_pdfs(company)]}"))
        return out


def _audit_real_lakehouse() -> list[tuple[str, bool, str]]:
    """离线档：审计真实数据目录里两家公司的 derived 产物。"""
    reader = LakehouseReader()
    out: list[tuple[str, bool, str]] = []
    for market, code in PAIRS:
        out += audit_derived(reader.company_dir(market, code), f"[{market}/{code}]")
    return out


def main() -> None:
    import argparse
    p = argparse.ArgumentParser(
        description="环节·解析验证（默认审计真实数据目录产物；--live 起 vllm 完整解析两地样本）")
    p.add_argument("--live", action="store_true",
                   help="起 vllm，把两地最新年报前几页完整解析进临时 lakehouse 再审计")
    args = p.parse_args()

    if args.live:
        checks: list[tuple[str, bool, str]] = []
        with_vllm(lambda: checks.extend(live_checks()))
    else:
        checks = _audit_real_lakehouse()
    print()
    report(checks)


if __name__ == "__main__":
    main()