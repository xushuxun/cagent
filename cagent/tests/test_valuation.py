"""估值环节测试：清算价值与数字表格的机械处理（默认离线，零 LLM）。

    uv run python cagent/tests/test_valuation.py            # 离线：程序侧算术与恒等式
    uv run python cagent/tests/test_valuation.py --live     # 加跑真实年报的清算折扣

离线验收标准全是程序侧的算术与恒等式——模型只出折扣率，折扣率本身的语义对错不在这里判：
1. 聚合行（分类小计、资产总计）不进折扣清单，明细一项不少，否则清算价值重复计入；
2. 金额恰好相同的不同科目都保留（同值 ≠ 同科目）；
3. 清算清单只取资产负债表的资产段——利润表与现金流量表的大额行不是资产；
4. 清单合计超过资产总计即判不适用（重复计入或非资产科目混入），不足只披露；
5. 折扣率越界、模型漏项、一条都不给，都如实进告警，不产出负数清算价值；
6. 展示口径与概念一致：合并净利润不写成归母净利润；DCF 与手算公式一致。

--live 验收标准（需要 llm 服务，单份年报约 2-3 分钟）：真实清单口径不越上界、折扣率落在
0-1、覆盖全部科目、多次采样结果稳定——温度不为 0 时这一条是提示词改动的回归网。
"""

import types

from cagent.lib.accounting import AccountingBuilder, AccountingTable, _drop_aggregates
from cagent.lib.lakehouse import LakehouseReader
from cagent.lib.valuation import (
    _NON_ASSET_CONCEPTS,
    _NON_ASSET_MARKERS,
    LIQ_MAX_ITEMS,
    Haircut,
    LiquidationAssessor,
    LiquidationResult,
    build_financials,
    dcf_matrix_md,
    dcf_value,
)
from cagent.tests._util import base_parser, connect, pick_filing, report, with_llm

# 一张带分类小计与总计的合并资产负债表（亿），行序 = 原文行序。故意埋两处同值：
# 货币资金/短期借款/应付账款 都是 300，应收账款/无形资产 都是 200——按值去重会误删。
BS = [("货币资金", 300.0), ("应收账款", 200.0), ("存货", 250.0), ("流动资产合计", 750.0),
      ("固定资产", 900.0), ("无形资产", 200.0), ("商誉", 80.0), ("非流动资产合计", 1180.0),
      ("资产总计", 1930.0),
      ("短期借款", 300.0), ("应付账款", 300.0), ("流动负债合计", 600.0),
      ("长期借款", 200.0), ("负债合计", 800.0),
      ("股本", 1000.0), ("未分配利润", 130.0), ("所有者权益合计", 1130.0)]
CONCEPT_OF = {"资产总计": "total_assets", "负债合计": "total_liabilities",
              "所有者权益合计": "total_equity"}
AGGREGATES = {"流动资产合计", "非流动资产合计", "资产总计",
              "流动负债合计", "负债合计", "所有者权益合计"}
ASSET_LEAVES = ["货币资金", "应收账款", "存货", "固定资产", "无形资产", "商誉"]
NON_ASSET_LEAVES = ["短期借款", "应付账款", "长期借款", "股本", "未分配利润"]
# 利润表/现金流量表的大额行：都不是资产，混进清算清单会让合计远超资产总计
OTHER_STMTS = [("营业总收入", 2228.2), ("利润总额", 117.6),
               ("经营现金流入小计", 2600.0), ("期末现金及现金等价物余额", 400.0)]
OTHER_KINDS = {"营业总收入": "is", "利润总额": "is",
               "经营现金流入小计": "cf", "期末现金及现金等价物余额": "cf"}


def make_acct(rows: list[tuple[str, float]], year: str = "2025",
              kinds: dict[str, str] | None = None, provenance: bool = True) -> AccountingTable:
    """按原文行序造一张数字表（概念行走 values，其余走 extra，order 保留行序）。

    provenance=True 时每行都带来源主表（kinds 未标明的按 bs 算），模拟真实数字表；
    provenance=False 则不给来源表，用来验「认不出来源表就退回全量 + 清算 fail-closed」
    这条退路。
    """
    values: dict[str, dict[str, float]] = {}
    extra: dict[str, dict[str, float]] = {}
    for label, v in rows:
        key = CONCEPT_OF.get(label, label)
        (values if key in CONCEPT_OF.values() else extra)[key] = {year: v}
    kinds_map = {CONCEPT_OF.get(lb, lb): (kinds or {}).get(lb, "bs") for lb, _ in rows} \
        if provenance else {}
    return AccountingTable(
        values=values, extra=extra, currency="CNY", years=[year],
        label_names={c: lb for lb, c in CONCEPT_OF.items()},
        order=[CONCEPT_OF.get(lb, lb) for lb, _ in rows],
        kinds=kinds_map)


def stub(reply: LiquidationResult):
    """把 LLM 那一步换成固定回复，只测程序侧机械处理；返回 (评估器, 它收到的入参)。"""
    seen: dict[str, str] = {}

    def fake(**kw):
        seen.update(kw)
        return types.SimpleNamespace(result=reply)

    assessor = LiquidationAssessor()
    # 测试桩：换成固定回复，只测程序侧的机械处理（折扣率语义对错不在离线验收标准内）
    assessor.assess = fake  # ty: ignore[invalid-assignment]
    return assessor, seen


def full_haircuts(labels: list[str] | None = None,
                  ratio: float = 1.0) -> LiquidationResult:
    return LiquidationResult(
        applicable=True, note="制造业",
        haircuts=[Haircut(label=lb, ratio=ratio) for lb in (labels or ASSET_LEAVES)])


def aggregate_checks() -> list[tuple[str, bool, str]]:
    """聚合行识别：小计/总计剔除、明细保全、同值不误删。"""
    leaves, dropped = _drop_aggregates([(CONCEPT_OF.get(lb, lb), lb, v) for lb, v in BS])
    names = [d for _k, d, _v in leaves]
    rows, alerts = make_acct(BS).latest_label_values()
    return [
        ("聚合行全部剔除", AGGREGATES <= set(dropped),
         f"剔除 {len(dropped)} 行：{'、'.join(dropped)}"),
        ("明细一项不少", all(n in names for n in ASSET_LEAVES + NON_ASSET_LEAVES),
         f"保留 {len(names)} 行"),
        ("同值不同科目都保留",
         names.count("应收账款") == 1 and "无形资产" in names and names.count("货币资金") == 1,
         "应收账款 200 与无形资产 200 并存，货币资金 300 与短期借款 300 并存"),
        ("聚合行没被当成明细留下", not (AGGREGATES & set(names)), f"残留 {AGGREGATES & set(names)}"),
        ("资产段只含资产明细",
         {d for _k, d, _v in rows} == set(ASSET_LEAVES), f"{len(rows)} 行"),
        ("正常运作不产生告警", alerts == [], f"{alerts}"),
    ]


def statement_scope_checks() -> list[tuple[str, bool, str]]:
    """来源表划界：利润表/现金流量表的大额行不是资产，不能进清算清单。"""
    rows = [*BS, *OTHER_STMTS]
    scoped, alerts = make_acct(rows, kinds=OTHER_KINDS).latest_label_values()
    names = [d for _k, d, _v in scoped]
    blind, _ = make_acct(rows, provenance=False).latest_label_values()
    res, md = stub(full_haircuts([d for _k, d, _v in blind]))[0](
        make_acct(rows, provenance=False), unit="亿")
    return [
        ("清算清单不含利润表现金流量表科目",
         not (set(names) & {lb for lb, _ in OTHER_STMTS}), f"{len(names)} 行：{'、'.join(names)}"),
        ("划界后清单合计 = 资产总计",
         abs(sum(v for _k, _d, v in scoped) - 1930.0) < 0.05 and alerts == [],
         f"合计 {sum(v for _k, _d, v in scoped):.1f}"),
        ("认不出来源表时非资产行漏进清单",
         {"利润总额", "期末现金及现金等价物余额"} <= {d for _k, d, _v in blind},
         f"{len(blind)} 行（超过资产总计的营业总收入等已被单项上界剔除）"),
        ("退回全量后由上界对账 fail-closed",
         not res.get("applicable") and "| **清算价值**" not in md
         and any("超过资产总计" in str(a) for a in res.get("alerts", [])),
         f"{res.get('note')}"),
    ]


def liquidation_checks() -> list[tuple[str, bool, str]]:
    """清算价值：口径正确、fail-closed、告警如实。"""
    acct = make_acct(BS)
    assessor, seen = stub(full_haircuts())
    res, md = assessor(acct, unit="亿")
    digest = seen.get("digest", "")
    checks = [
        ("送给模型的清单只含资产明细",
         all(f"- {lb}:" in digest for lb in ASSET_LEAVES)
         and not any(lb in digest for lb in NON_ASSET_LEAVES + list(AGGREGATES)),
         f"{digest.count(chr(10)) + 1} 行"),
        ("折扣率全 1 时清算价值 = 资产总计 − 总负债",
         res.get("applicable") and abs(res["liquidation_value"] - (1930.0 - 800.0)) < 0.05,
         f"{res.get('liquidation_value')} vs 1130.0（= 账面权益）"),
        ("表格逐行列出明细与总负债",
         all(lb in md for lb in ASSET_LEAVES) and "| 总负债 |" in md and "| **清算价值**" in md,
         f"{md.count(chr(10)) + 1} 行"),
        ("干净清单不产生告警", res.get("alerts") == [], f"{res.get('alerts')}"),
    ]

    # 模型漏项：只给 3 项折扣 → 其余按 0 变现（偏低）并披露
    res2, _ = stub(full_haircuts(ASSET_LEAVES[:3]))[0](acct, unit="亿")
    want2 = 300.0 + 200.0 + 250.0 - 800.0
    checks.append(("模型漏项：按 0 变现并披露",
                   res2.get("applicable") and abs(res2["liquidation_value"] - want2) < 0.05
                   and any("未给折扣" in a for a in res2.get("alerts", [])),
                   f"{res2.get('liquidation_value')} vs {want2}，告警 {len(res2.get('alerts', []))} 条"))

    # 一条折扣都不给：判不适用，不产出「清算价值 = −总负债」的表
    res3, md3 = stub(LiquidationResult(applicable=True, note="制造业"))[0](acct, unit="亿")
    checks.append(("零折扣：判不适用而非产出负数表",
                   not res3.get("applicable") and "| **清算价值**" not in md3,
                   f"applicable={res3.get('applicable')}，{res3.get('note')}"))

    # 折扣率越界与百分数写法
    res4, _ = stub(LiquidationResult(applicable=True, haircuts=[
        Haircut(label="货币资金", ratio=60),        # 当成 0.6
        Haircut(label="应收账款", ratio=150),       # 越界，弃用
        Haircut(label="存货", ratio=-0.5),          # 越界，弃用
        Haircut(label="商誉", ratio=0.0),           # 合法
        Haircut(label="不存在的科目", ratio=1.0),    # 不在清单
    ]))[0](acct, unit="亿")
    a4 = res4.get("alerts", [])
    checks.append(("折扣率：60 当 0.6、越界弃用、清单外科目跳过",
                   abs(res4["line_items"][0]["ratio"] - 0.6) < 1e-9
                   and len(res4["line_items"]) == 2
                   and sum("越界" in x for x in a4) == 2
                   and any("不在清单内" in x for x in a4),
                   f"采用 {len(res4['line_items'])} 项，告警 {len(a4)} 条"))

    # 明细缺行使分类小计无法用求和恒等式识别（固定资产那一行没解析出来）→ 小计与明细
    # 并列进清单 → 合计超过资产总计 → 判不适用，而不是产出虚高的清算价值
    leak = make_acct([r for r in BS if r[0] != "固定资产"])
    res5, md5 = stub(full_haircuts(["货币资金", "应收账款", "存货",
                                    "无形资产", "商誉", "非流动资产合计"]))[0](leak, unit="亿")
    checks.append(("清单超过资产总计：判不适用（不产出虚高数字）",
                   not res5.get("applicable") and "| **清算价值**" not in md5
                   and any("超过资产总计" in a for a in res5.get("alerts", [])),
                   f"{res5.get('note')}"))

    # 明细多于清单上限 → 截断，合计低于资产总计：保守方向，只披露不拦
    many = [(f"明细资产{i:02d}", 40.0) for i in range(LIQ_MAX_ITEMS + 5)]
    trunc = make_acct([*many, ("资产总计", 40.0 * len(many)), ("短期借款", 500.0),
                       ("负债合计", 500.0), ("股本", 1300.0), ("所有者权益合计", 1300.0)])
    res6, _ = stub(full_haircuts([lb for lb, _ in many[:LIQ_MAX_ITEMS]]))[0](trunc, unit="亿")
    a6 = res6.get("alerts", [])
    checks.append(("清单超长：截断且缺项只披露",
                   res6.get("applicable") and any("只取前" in a for a in a6)
                   and any("清单缺项" in a for a in a6),
                   f"告警 {len(a6)} 条：{'；'.join(a6)[:80]}"))
    return checks


def table_checks() -> list[tuple[str, bool, str]]:
    """数字表格：DCF 与手算一致、展示口径与概念一致。"""
    checks = [("DCF 零增长 = base ÷ r", abs(dcf_value(100.0, 0.0, 0.10) - 1000.0) < 1e-6,
               f"{dcf_value(100.0, 0.0, 0.10):.4f} vs 1000")]
    want = sum(100 * 1.05 ** (t - 1) / 1.1 ** t for t in range(1, 6)) \
        + 100 * 1.05 ** 4 / 0.10 / 1.1 ** 5
    checks.append(("DCF 5% 增长与手算一致", abs(dcf_value(100.0, 0.05, 0.10) - want) < 1e-6,
                   f"{dcf_value(100.0, 0.05, 0.10):.4f} vs {want:.4f}"))
    md = dcf_matrix_md(100.0, 80.0, 120.0, 0.10)
    checks.append(("DCF 矩阵列出全部起点组合",
                   all(s in md for s in ("年均净利润", "年均自由现金流", "最新年自由现金流"))
                   and md.count("|") > 12, f"{len(md.splitlines())} 行"))
    checks.append(("DCF 起点缺失时不硬算", "无法计算" in dcf_matrix_md(None, None, None, 0.10), ""))

    fin = build_financials({"revenue": {"2024": 1000.0, "2025": 1200.0},
                            "gross_profit": {"2024": 300.0, "2025": 1500.0},  # 毛利 > 收入
                            "net_profit": {"2024": 100.0, "2025": 120.0},
                            "total_equity": {"2024": 800.0, "2025": 900.0}},
                           currency="CNY")
    checks += [
        ("净利润不标成归母净利润",
         "归母净利润" not in fin["markdown"] and "净利润" in fin["markdown"],
         "net_profit 是合并口径，含少数股东损益"),
        ("ROE 用平均权益", abs(fin["ratios"]["roe"]["2025"] - 120.0 / 850.0) < 1e-9,
         f"{fin['ratios']['roe']['2025']:.4f} vs {120.0 / 850.0:.4f}"),
        ("毛利大于收入时置空不猜",
         fin["ratios"]["gross_margin"]["2025"] is None
         and abs(fin["ratios"]["gross_margin"]["2024"] - 0.3) < 1e-9,
         "2025 毛利 1500 > 收入 1200"),
        ("对账表给出起点区间与禁区",
         "正常化股东盈余" in fin["owner_earnings"]["markdown"]
         and "不得直接以单年" in fin["owner_earnings"]["markdown"], ""),
    ]
    return checks


def live_checks(market: str, code: str, file: str | None,
                runs: int = 2) -> list[tuple[str, bool, str]]:
    """真实年报上的清算折扣（需要 llm 服务）：口径、覆盖率、多次采样稳定性。"""
    reader = LakehouseReader()
    filing, derived = pick_filing(reader, market, code, file)
    print(f"公告: {filing['date']} {filing['title']}（{filing['file']}）")
    connect(False, "valuation")  # 只为把服务配到 dspy 全局，清算走全局 LM
    acct = AccountingBuilder()(derived, [filing])
    if not acct or not acct.years:
        return [("真实年报：数字表产出", False, "先跑 test_accounting.py 排查")]

    rows, _alerts = acct.latest_label_values()
    assets = [(d, v) for _k, d, v in rows
              if _k not in _NON_ASSET_CONCEPTS
              and not any(m in d for m in _NON_ASSET_MARKERS)][:LIQ_MAX_ITEMS]
    cap = acct.values.get("total_assets", {}).get(acct.years[-1])
    listed = sum(v for _d, v in assets)
    checks = [("真实年报：资产明细清单不越上界",
               cap is not None and listed <= cap * 1.02,
               f"清单 {listed:.1f} vs 资产总计 {cap:.1f}（{len(assets)} 项）")]

    results = []
    for _ in range(runs):
        res, _md = LiquidationAssessor()(acct, unit=f"亿{acct.currency}")
        results.append(res)
    first = results[0]
    if not first.get("applicable"):
        return checks + [("真实年报：清算法适用性", False,
                          f"判不适用——{first.get('note')}；告警 {first.get('alerts')}")]
    ratios = [it["ratio"] for it in first["line_items"]]
    alerts = [str(a) for a in first.get("alerts", [])]
    values = [r["liquidation_value"] for r in results if r.get("applicable")]
    spread = max(values) - min(values) if values else 0.0
    return checks + [
        ("真实年报：折扣率全部落在 0-1", bool(ratios) and all(0 <= r <= 1 for r in ratios),
         f"{len(ratios)}/{len(assets)} 项，区间 [{min(ratios):.2f}, {max(ratios):.2f}]"),
        ("真实年报：覆盖清单全部科目", not any("未给折扣" in a for a in alerts),
         f"告警 {len(alerts)} 条"),
        ("真实年报：清算价值为正且不超过账面权益",
         0 < first["liquidation_value"] <= (acct.values.get("total_equity", {})
                                            .get(acct.years[-1]) or 0) * 1.05,
         (f"{first['liquidation_value']} vs 账面权益 "
          f"{acct.values.get('total_equity', {}).get(acct.years[-1])}")),
        (f"真实年报：{runs} 次采样结果一致",
         len(values) == runs and spread <= max(1.0, abs(values[0]) * 0.05),
         f"{values}，极差 {spread:.1f}（清算折扣走确定性解码）"),
    ]


def main() -> None:
    p = base_parser("估值环节：清算与数字表格自检（默认离线，--live 加跑真实年报）")
    p.add_argument("--live", action="store_true", help="加跑真实年报的清算折扣（需要 llm 服务）")
    p.add_argument("--runs", type=int, default=2, help="--live 时采样次数（默认 2，看稳定性）")
    args = p.parse_args()

    checks = (aggregate_checks() + statement_scope_checks()
              + liquidation_checks() + table_checks())
    if args.live:
        live: list = []
        with_llm(lambda: live.extend(
            live_checks(args.market, args.code, args.file, runs=args.runs)))
        checks += live
    print()
    report(checks)


if __name__ == "__main__":
    main()
