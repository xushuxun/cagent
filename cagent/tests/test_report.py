"""报告测试：单份年报走 agent.py 的真实阶段函数（不另抄一套装配）。

    uv run python cagent/tests/test_report.py --market cn --code 601633 [--trace]

无缓存：每次全量重算（定性摘要是大头，单份数十分钟）。
验收标准：无残留占位符/模板注释、关键数字入报告、证据合并成功、异常清单里没有「调用失败」
（检出不一致本身不算失败，只如实登记）。报告写在 output/ 下，不碰 reports/ 的正式产物。
"""

from pathlib import Path

from cagent.agent import (
    Ctx,
    _ensure_tocs,
    _fin_and_tables,
    run_accounting_stage,
    run_evidence_stage,
    run_report_stage,
)
from cagent.lib.lakehouse import LakehouseReader
from cagent.tests._util import base_parser, connect, pick_filing, report, with_llm

TEMPLATE = Path("templates/template_business_model_single_company.md")
OUTPUT = Path("output/test_report.md")


def main() -> None:
    args = base_parser("报告：全链渲染与对账自检（走真实阶段函数）").parse_args()
    reader = LakehouseReader()
    filing, derived = pick_filing(reader, args.market, args.code, args.file)
    print(f"公告: {filing['date']} {filing['title']}（{filing['file']}）")

    def run() -> None:
        lm = connect(args.trace, "report")
        ctx = Ctx(lm=lm, reader=reader, market=args.market, code=args.code, company_name="",
                  template_text=TEMPLATE.read_text(encoding="utf-8"), output=OUTPUT,
                  derived=derived, filings=[filing])

        acct = run_accounting_stage(ctx)
        if not acct or not acct.years:
            raise SystemExit("数字表提取失败，先跑 test_accounting.py 排查")
        fin, tables = _fin_and_tables(ctx, acct)
        toc_alerts = _ensure_tocs(ctx)
        evidence, quals = run_evidence_stage(ctx, fin, acct)
        issues = run_report_stage(ctx, evidence, acct, fin, tables, quals, toc_alerts)

        content = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        latest = acct.years[-1]
        revenue = acct.values.get("revenue", {}).get(latest)
        checks = [
            ("渲染产出非空", len(content) > 3000, f"{len(content)} 字符"),
            ("无残留占位符", "{{" not in content, "占位符全部替换"),
            ("无残留模板注释", "<!--" not in content, "HTML 注释已剥离"),
            ("最新年收入入报告", revenue is not None and f"{revenue:.1f}" in content,
             f"{latest} 收入 {revenue and round(revenue, 1)}"),
            ("证据合并成功", evidence is not None, "失败会回退直写并记入异常清单"),
            ("对账未失败", not any(i["where"] == "保真对账" for i in issues),
             f"异常清单 {len(issues)} 条"),
        ]
        for it in issues[:8]:
            print(f"  [{it['where']}] {it['item']}: {it['problem']}")
        print()
        report(checks)

    with_llm(run)


if __name__ == "__main__":
    main()
