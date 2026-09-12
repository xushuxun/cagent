"""证据合并测试：单份年报进程内组合（accounting → toc/qual → 结构化证据 JSON）。

    uv run python cagent/tests/test_evidence.py --market cn --code 601633 [--trace]

验收标准：顶层键齐（integrity/business/fcf_analysis/moat/management/data_gaps）、
结论带来源标注。无缓存：每次全量重算（定性摘要是大头）。
"""

import json

from cagent.agent import EvidenceComposer
from cagent.lib.accounting import AccountingBuilder
from cagent.lib.lakehouse import LakehouseReader
from cagent.lib.qual import build_qual
from cagent.lib.valuation import anchor_text, build_financials
from cagent.pageindex.builder import PageIndexBuilder
from cagent.tests._util import base_parser, connect, pick_filing, report, with_llm

REQUIRED_KEYS = {"integrity", "business", "fcf_analysis", "moat", "management", "data_gaps"}


def main() -> None:
    args = base_parser("证据合并：摘要 → 证据 JSON 自检").parse_args()
    reader = LakehouseReader()
    filing, derived = pick_filing(reader, args.market, args.code, args.file)
    print(f"公告: {filing['date']} {filing['title']}（{filing['file']}）")

    def run() -> None:
        lm = connect(args.trace, "evidence")
        acct = AccountingBuilder()(derived, [filing])
        anchor = "（测试未提供锚表）"
        if acct and acct.years:
            fin = build_financials(acct.values, currency=acct.currency or None, table_tail=5)
            anchor = anchor_text(fin)
        PageIndexBuilder(reader).build(derived / filing["file"])  # .pageindex.json 构建产物（已存在即复用）
        quals = build_qual(lm, reader, derived, [filing])
        if not quals:
            report([("摘要产出", False, "build_qual 返回空，先跑 test_qual.py 排查")])
            return
        evidence = EvidenceComposer()(lm, args.code, args.market, quals, anchor)

        checks = [("证据 JSON 产出", isinstance(evidence, dict), "")]
        if isinstance(evidence, dict):
            checks.append(("顶层键齐", REQUIRED_KEYS.issubset(evidence),
                           f"缺 {sorted(REQUIRED_KEYS - set(evidence))}"))
            checks.append(("fcf_analysis 非空", bool(evidence.get("fcf_analysis")), ""))
            checks.append(("结论带来源标注", "p." in json.dumps(evidence, ensure_ascii=False),
                           "(年报YYYY, p.N) 格式"))
        print()
        report(checks)

    with_llm(run)


if __name__ == "__main__":
    main()
