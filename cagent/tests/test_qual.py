"""定性测试：单份年报 →（内存建 TOC）→ 章节定长摘要 + 管理层叙事。

    uv run python cagent/tests/test_qual.py --market cn --code 601633 [--trace]

验收标准：摘要非空且带页码索引 (p.N)；叙事 JSON 有 entries、每条含 year/management_said。
"""

import re

from cagent.lib.lakehouse import LakehouseReader
from cagent.lib.qual import build_qual
from cagent.pageindex.builder import PageIndexBuilder
from cagent.tests._util import base_parser, connect, pick_filing, report, with_llm


def main() -> None:
    args = base_parser("定性：单份年报摘要与叙事自检").parse_args()
    reader = LakehouseReader()
    filing, derived = pick_filing(reader, args.market, args.code, args.file)
    print(f"公告: {filing['date']} {filing['title']}（{filing['file']}）")

    def run() -> None:
        lm = connect(args.trace, "qual")
        PageIndexBuilder(reader).build(derived / filing["file"])  # .pageindex.json 构建产物（已存在即复用）
        quals = build_qual(lm, reader, derived, [filing])

        checks = [("定性产出", len(quals) == 1, f"{len(quals)} 份")]
        if not quals:
            report(checks)
            return
        q = quals[0]
        checks += [
            ("摘要非空", bool(q["summaries"].strip()), f"{len(q['summaries'])} 字符"),
            ("摘要带页码索引", bool(re.search(r"\(p\.\d+\)", q["summaries"])), "格式 (p.N)"),
            ("叙事 entries 非空",
             isinstance(q.get("narrative"), dict) and bool(q["narrative"].get("entries")),
             f"{len((q.get('narrative') or {}).get('entries') or [])} 条"),
        ]
        entries = (q.get("narrative") or {}).get("entries") or []
        if entries:
            e = entries[0]
            checks.append(("叙事字段齐", bool(e.get("year")) and bool(e.get("management_said")),
                           f"year={e.get('year')}"))

        print("\n摘要开头:")
        print("\n".join(q["summaries"].splitlines()[:8]))
        print()
        report(checks)

    with_llm(run)


if __name__ == "__main__":
    main()
