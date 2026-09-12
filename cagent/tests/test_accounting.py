"""定量测试：单份年报 → 概念数字表 + 结构化财务表格集。判据全部来自真实年报与真实 LLM。

    uv run python cagent/tests/test_accounting.py --market cn --code 601633 [--trace]

1. 主表 GT 逐格（14 格，数值取自年报原文人工核对）——恒等式三件套互相咬合，错一格必破；
2. 恒等式逐年成立（1% 容差，披露口径，不修复）；
3. 表格集统计：结构化表数、值列数、sum/tied 证据列数、无证据（说明性）表数、值格解析率；
4. tied 的附注合计必须能回原文核对：该表所标页码那一页确实印着这个数；
5. **LLM 问答**（以回答质量评判）：把结构化表喂给模型做算术问答（合计、最大项、占比），
   答案须与人工核对的数值一致——提取出的表要真能被读懂、算对，而不是只「看起来结构化」。
"""

import dspy

from cagent.lib.accounting import (
    VERIFY_NONE,
    VERIFY_SUM,
    VERIFY_TIED,
    AccountingBuilder,
    FinTable,
)
from cagent.lib.lakehouse import LakehouseReader
from cagent.llm import DETERMINISTIC, tag
from cagent.tests._util import base_parser, connect, pick_filing, report, with_llm

# 主表锚点（亿）：cn 601633 与 hk 02333 是同一家公司 A/H，同一准则，应逐格一致
_GT_GWM = {
    ("total_assets", "2024"): 2177.20, ("total_assets", "2025"): 2252.88,
    ("total_liabilities", "2024"): 1387.27, ("total_liabilities", "2025"): 1373.96,
    ("total_equity", "2024"): 789.93, ("total_equity", "2025"): 878.92,
    ("revenue", "2024"): 2021.94, ("revenue", "2025"): 2228.24,
    ("net_profit", "2024"): 126.60, ("net_profit", "2025"): 98.65,
    ("operating_cash_flow", "2024"): 277.71, ("operating_cash_flow", "2025"): 403.55,
    ("capex", "2024"): 118.78, ("capex", "2025"): 115.09,
}
# 附注合计 → 主表同科目（tied）样例：(表名, 年份, 期望亿值, 原文数字串)
# 表名给繁简两种写法：A/H 两份年报同号数字（都到元级），但港股正文是繁体
_TIED_GWM: list[tuple[tuple[str, ...], str, float, str]] = [
    (("存货", "存貨"), "2025", 261.48, "26,147,992,041.55"),
    (("短期借款", "短期借款"), "2025", 65.32, "6,531,885,229.35"),
    (("销售费用", "銷售費用"), "2025", 112.73, "11,273,114,891.99"),
]
# LLM 问答案例：(表名写法, 年份, 问题, [每项事实的可接受写法])——数值均人工核对原文
_QA_GWM: list[tuple[tuple[str, ...], str, str, list[tuple[str, ...]]]] = [
    (("存货", "存貨"), "2025",
     ("按这张存货附注表：(1) 2025 年末存货账面价值合计多少亿元？"
      "(2) 账面价值最大的存货类别是哪个？(3) 它占账面余额合计的百分比是多少？"),
     [("261.48", "261.5"), ("产成品", "產成品"), ("69.2", "69.23", "69.3")]),
    (("销售费用", "銷售費用"), "2025",
     ("按这张销售费用附注表：2025 年度金额最大的两个构成项目，合计占该科目合计的百分比"
      "是多少？并给出这两个项目名称。"),
     [("64.1", "64.08", "64.2"), ("广告", "廣告"), ("工资", "工資", "薪酬")]),
]
CHECKS: dict[tuple[str, str], tuple[dict, list, list]] = {
    ("cn", "601633"): (_GT_GWM, _TIED_GWM, _QA_GWM),
    ("hk", "02333"): (_GT_GWM, _TIED_GWM, _QA_GWM),
}


class AnswerTable(dspy.Signature):
    """根据给定的财务表格回答问题。只能用表里的数字；要自己做的加法与除法保留两位小数。

    表里没有的填「未披露」，不要引入外部知识；金额单位已在表名里标出（值为亿）。
    """

    table: str = dspy.InputField(desc="结构化财务表（markdown，列名带期间路径与可验证性标注）")
    question: str = dspy.InputField(desc="要回答的问题")
    answer: str = dspy.OutputField(desc="简明回答，含数值与百分比")


def _find(fins: list[FinTable], needles: tuple[str, ...],
          year: str) -> FinTable | None:
    """按表名与期间找一张合并口径表（该年要有被恒等式确认的合计行才算找到）。"""
    for ft in fins:
        if ft.entity != "con" or not any(n in ft.title for n in needles):
            continue
        if any(c.spec.year == year and c.total_row is not None for c in ft.columns):
            return ft
    return None


def table_checks(acct) -> list[tuple[str, bool, str]]:
    """表格集判据：规模、算术证据、量纲分级、解析率、页码出处。"""
    fins = acct.tables
    ncol = sum(len(ft.columns) for ft in fins)
    ties = sum(1 for ft in fins for c in ft.columns if c.verify == VERIFY_TIED)
    sums = sum(1 for ft in fins for c in ft.columns if c.verify == VERIFY_SUM)
    plain = sum(1 for ft in fins if all(c.verify == VERIFY_NONE for c in ft.columns))
    cells = sum(ft.value_cells for ft in fins)
    parsed = sum(ft.parsed_cells for ft in fins)
    cats: dict[str, int] = {}
    for ft in fins:
        cats[ft.category] = cats.get(ft.category, 0) + 1
    stmts = " ".join(f"{ft.category}={len(ft.rows)}行" for ft in fins
                     if ft.is_statement and ft.entity == "con")
    kinds = " ".join(f"{k}:{v}" for k, v in sorted(cats.items()))
    return [
        ("财务表格集产出", len(fins) >= 200,
         f"{len(fins)} 张 / 行 {sum(len(ft.rows) for ft in fins)} / 值列 {ncol} / {kinds}"),
        ("主表四表齐备（合并口径）",
         all(any(ft.category == k and ft.entity == "con" for ft in fins)
             for k in ("bs", "is", "cf", "cfs")), stmts),
        ("算术证据（tied 与 sum）", ties >= 20 and sums >= 150,
         (f"tied {ties} 列（与主表勾稽上）、sum {sums} 列（表内合计恒等式成立）、"
          f"无证据 {ncol - ties - sums} 列")),
        ("说明性表格单独标出", plain >= 50,
         (f"{plain} 张表无任何会计证据（说明性数据，不得当已验证财务数字）；"
          f"非金额表 {sum(1 for ft in fins if not ft.money)} 张未定标")),
        ("值格解析率", parsed >= cells * 0.97,
         f"{parsed}/{cells} = {parsed / cells:.1%}（差额逐表披露，不静默丢数）"),
        ("每张表带页码出处", all(ft.page is not None for ft in fins),
         (f"页码 {min((ft.page for ft in fins), default=0)}"
          f"-{max((ft.page for ft in fins), default=0)}")),
    ]


def tied_checks(acct, market: str, code: str,
                pages: dict[int, str]) -> list[tuple[str, bool, str]]:
    """附注合计与主表勾稽上的列，还要能回原文那一页核对。

    同一科目往往有几张附注表（「短期借款」与「短期借款和长期借款」）、每张又可能有
    多个口径列（账面余额/减值准备/账面价值），所以要找的是**存在**一个勾稽一致且原文
    可核对的列，而不是拿第一张表的第一列去比。
    """
    out = []
    for needles, year, want, raw in CHECKS[(market, code)][1]:
        hits = [(ft, col, col.values[col.total_row]) for ft in acct.tables
                if ft.entity == "con" and any(n in ft.title for n in needles)
                for col in ft.columns
                if col.spec.year == year and col.total_row is not None
                and col.verify == VERIFY_TIED]
        hit = next((h for h in hits if h[2] is not None and abs(h[2] - want) / want < 0.005
                    and raw in pages.get(h[0].page or 0, "")), None)
        cand = "; ".join(f"「{ft.title}」p{ft.page} " +
                         ",".join(f"{c.spec.year}合计{round(c.values[c.total_row], 2)}[{c.verify}]"
                                  for c in ft.columns if c.total_row is not None)
                         for ft in acct.tables
                         if ft.entity == "con" and any(n in ft.title for n in needles))
        out.append((f"附注合计 {needles[0]} {year}", hit is not None,
                    (f"{round(hit[2], 2)} 亿 tied，原文 {raw} 在 p{hit[0].page} ✓" if hit
                     else f"期望 {want} 亿；候选表：{cand or '无（表名没对上）'}")))
    return out


def qa_checks(acct, market: str, code: str) -> list[tuple[str, bool, str]]:
    """LLM 问答：结构化表能不能被读懂并算对（回答质量即提取质量）。"""
    answer = dspy.Predict(AnswerTable)
    out = []
    for needles, year, q, wants in CHECKS[(market, code)][2]:
        ft = _find(acct.tables, needles, year)
        if ft is None:
            out.append((f"问答 {needles[0]}", False, "表没找到（附注未被提取或表名不符）"))
            continue
        with tag(f"accounting:qa:{ft.title}"):
            got = answer(table=ft.markdown(rows=60), question=q,
                         config=DETERMINISTIC | {"max_tokens": 600}).answer
        text = got.replace(" ", "")
        missed = [w for w in wants if not any(a in text for a in w)]
        detail = (f"漏 {len(missed)} 项 {missed}" if missed else f"{len(wants)} 项全中")
        out.append((f"问答 {needles[0]}（{len(ft.rows)}行{len(ft.columns)}列）",
                    not missed, f"{detail}：{got[:110]}"))
    return out


def main() -> None:
    p = base_parser("定量：概念数字表与财务表格集自检（真实年报 + 真实 LLM）")
    args = p.parse_args()
    reader = LakehouseReader()
    filing, derived = pick_filing(reader, args.market, args.code, args.file)
    print(f"公告: {filing['date']} {filing['title']}（{filing['file']}）")

    def run() -> None:
        connect(args.trace, "accounting")
        acct = AccountingBuilder()(derived, [filing])
        got = f"{len(acct.values) if acct else 0} 概念 / 年份 {acct.years if acct else []}"
        checks = [("数字表产出", acct is not None and bool(acct.years),
                   got + f" / 币种 {acct and acct.currency}")]
        if not acct or not acct.years:
            report(checks)
            return
        gt = CHECKS.get((args.market, args.code), ({}, [], []))[0]
        for (k, y), exp in sorted(gt.items()):
            v = acct.values.get(k, {}).get(y)
            checks.append((f"GT {k} {y}", v is not None and abs(v - exp) / exp < 0.005,
                           f"{v and round(v, 2)} vs 期望 {exp}"))
        for y in acct.years:
            a, li, eq = (acct.values.get(k, {}).get(y)
                         for k in ("total_assets", "total_liabilities", "total_equity"))
            if a and li and eq:
                checks.append((f"恒等式 {y}", abs(a - li - eq) <= abs(a) * 0.01,
                               f"残差 {a - li - eq:+.2f} 亿"))
        pages = reader.pages_of((derived / filing["file"]).read_text(encoding="utf-8"))
        checks += table_checks(acct)
        checks += tied_checks(acct, args.market, args.code, pages)
        checks += qa_checks(acct, args.market, args.code)
        checks.append(("恒等式无告警", not acct.identity_alerts,
                       (f"恒等式告警 {len(acct.identity_alerts)} 条；提取检出 "
                        f"{len(acct.stage_alerts)} 条")))

        print("\n关键概念：")
        for k in ("revenue", "net_profit", "operating_cash_flow", "capex", "total_assets",
                  "total_liabilities", "total_equity", "interest_bearing_debt"):
            s = acct.values.get(k, {})
            print(f"  {k:22s} " + "  ".join(f"{y}:{v:,.2f}" for y, v in sorted(s.items())))
        print(f"\n主表之外未映射科目 {len(acct.extra)} 个（进 extra）")
        sample = _find(acct.tables, ("存货", "存貨"), "2025")
        print("\n样例结构化表（存货附注）：")
        print(sample.markdown(rows=6) if sample else "（未找到）")
        print("\n检出告警：")
        for a in acct.stage_alerts[:6]:
            print(f"  · {a[:150]}")
        print()
        report(checks)

    with_llm(run)


if __name__ == "__main__":
    main()
