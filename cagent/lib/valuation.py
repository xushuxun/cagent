"""估值：数字表 → 锚表 / 盈利质量对账 / DCF 矩阵 / 清算价值。

数字表格全部程序渲染（AI 不写出数字）；判断类工作由 LiquidationAssessor
在独立上下文完成（只出折扣率与依据）。数字提取在 lib/accounting.py。
"""

import logging
from collections.abc import Mapping

import dspy
from pydantic import BaseModel

from cagent.lib.accounting import AccountingTable
from cagent.llm import DETERMINISTIC, tag

logger = logging.getLogger(__name__)

LIQ_MAX_ITEMS = 40  # 清算折扣清单最多列多少项资产明细（再多模型也逐条给不稳）

# 非资产/负债权益科目：清算折扣只能作用于资产科目（标准概念里其余都是负债、权益或流量）
_NON_ASSET_CONCEPTS = {
    "total_liabilities", "current_liabilities", "total_equity",
    "short_term_debt", "long_term_debt", "bonds_payable",
    "lease_liabilities", "interest_bearing_debt",
    "revenue", "gross_profit", "gross_margin", "operating_profit",
    "pre_tax_profit", "net_profit", "operating_cash_flow", "capex",
    "depreciation_amortization", "dividends_paid",
}
_NON_ASSET_MARKERS = (
    "负债", "借款", "应付", "预收", "合同负债", "租赁负债", "债券",
    "权益", "股本", "股东", "资本公积", "盈余公积", "未分配利润",
    "收入", "收益", "营业额", "利润", "费用", "现金流", "折旧",
    "摊销", "资本开支", "分红", "股利", "经营", "投资活动", "筹资",
)


def dcf_value(base: float, growth: float, r: float, years: int = 5) -> float:
    """股东盈余折现：显性预测 years 年 + 零增长永续终值（末期盈余 ÷ r）折回。"""
    flows = sum(base * (1 + growth) ** (t - 1) / (1 + r) ** t for t in range(1, years + 1))
    terminal = base * (1 + growth) ** (years - 1) / r / (1 + r) ** years
    return flows + terminal


def dcf_matrix_md(avg_np: float | None, avg_fcf: float | None, latest_fcf: float | None,
                  r: float, years: int = 5, unit: str = "亿") -> str:
    """DCF 敏感性矩阵：起点 × 增速全组合。每个值 = 该预测对应的企业价值；
    隐含稳态盈余 = 价值 × r。"""
    starts = [("年均净利润", avg_np), ("年均自由现金流", avg_fcf),
              ("最新年自由现金流", latest_fcf)]
    starts = [(n, v) for n, v in starts if v is not None]
    growths = [0.0, 0.05, 0.08]
    if not starts:
        return "（数字表数据不足，无法计算）"
    lines = [
        (f"折现口径：未来 {years} 年逐年按（1+{r:.0%}）折现，之后零增长永续，"
         f"终值 = 末期盈余 ÷ {r:.0%}。单位：{unit}。"),
        "",
        "| 起点＼年增速 | " + " | ".join(f"{g:.0%}" for g in growths) + " |",
        "|---|" + "---:|" * len(growths),
    ]
    for name, base in starts:
        cells = [f"{dcf_value(base, g, r, years):.0f}" for g in growths]
        lines.append(f"| {name} {base:.1f} | " + " | ".join(cells) + " |")
    lines += ["",
              ("读法：每个值对应一种预测——起点代表相信公司稳定能赚多少，"
               "增速代表相信它还能内在增长多快；与「盈利质量对账」的多年均值对照，"
               "自行判断哪一格更接近现实。隐含稳态盈余 = 表中值 × 要求回报率。")]
    return "\n".join(lines)


class Haircut(BaseModel):
    """一个科目的保守变现折扣：label 逐字取自输入清单，ratio 为 0-1 比例。"""

    label: str
    ratio: float
    rationale: str = ""


class LiquidationResult(BaseModel):
    """清算价值评估结果：applicable=False 时 haircuts 为空。"""

    applicable: bool
    note: str = ""
    haircuts: list[Haircut] = []


class Liquidation(dspy.Signature):
    """估算清算价值（下行保护）：对清单中的资产科目给保守变现比例与一句依据；
label 逐字取自清单。银行/保险/地产等杠杆经营主体判不适用。"""

    digest: str = dspy.InputField(desc="科目账面值清单，每行 '- 科目: 金额'")
    result: LiquidationResult = dspy.OutputField(desc="适用性判断 + 各科目折扣")


_LIQ_DEMO = dspy.Example(
    digest="最新年报资产负债表主要科目账面值（亿）：\n"
           "- 货币资金: 500.0\n- 存货: 300.0\n- 固定资产: 400.0\n- 商誉: 80.0",
    result={"applicable": True, "note": "制造业，资产变现折扣法适用",
            "haircuts": [{"label": "货币资金", "ratio": 1.0, "rationale": "现金全额可变现"},
                          {"label": "存货", "ratio": 0.6, "rationale": "滞销需折价清理"},
                          {"label": "固定资产", "ratio": 0.4, "rationale": "专用设备变现难"},
                          {"label": "商誉", "ratio": 0.0, "rationale": "清算时无价值"}]},
).with_inputs("digest")


class LiquidationAssessor(dspy.Module):
    """清算价值评估：账面值程序取（数字表最新年），LLM 只出折扣率与依据。

    清单用 label_names 展示原文中文科目名，避免英文概念名让模型误判。"""

    def __init__(self):
        super().__init__()
        self.assess = dspy.Predict(Liquidation)
        self.assess.demos = [_LIQ_DEMO]

    def forward(self, acct: AccountingTable, unit: str = "亿") -> tuple[dict, str]:
        rows, alerts = acct.latest_label_values()
        rows = [(display, v, key) for key, display, v in rows
                if key not in _NON_ASSET_CONCEPTS
                and not any(m in display for m in _NON_ASSET_MARKERS)]
        # 截断放在滤掉非资产科目之后：负债/权益明细金额天然大，先截断会把资产项挤出清单
        if len(rows) > LIQ_MAX_ITEMS:
            alerts.append(f"资产明细 {len(rows)} 项只取前 {LIQ_MAX_ITEMS} 项参与折扣，"
                          f"其余按 0 变现计入（清算价值偏低）")
            rows = rows[:LIQ_MAX_ITEMS]
        if not rows:
            return ({"applicable": False, "note": "数字表无最新年资产科目",
                     "alerts": alerts}, "（无数据）")
        digest = "\n".join(f"- {label}: {v:.1f}" for label, v, _ in rows)
        book: dict[str, tuple[str, float]] = {label: (key, v) for label, v, key in rows}
        liab = acct.values.get("total_liabilities", {}).get(acct.years[-1]) \
            if acct.years else None
        if liab is None:
            note = "数字表缺最新年负债总额，无法可靠计算清算价值"
            return ({"applicable": False, "note": note, "alerts": alerts},
                    f"清算法不适用：{note}")
        # 上界对账（放在调用模型之前：清单本身不可信就别花 decode）。方向敏感——
        # 合计超过资产总计 = 重复计入或非资产科目混入，算出来的清算价值会虚高，直接判
        # 不适用；合计不足 = 清单缺项，对「下行保护」是保守方向，只披露不拦。
        cap = acct.values.get("total_assets", {}).get(acct.years[-1])
        listed = sum(v for _l, v, _k in rows)
        if cap and listed > cap * 1.02:
            note = (f"清算清单合计 {listed:.1f} 超过资产总计 {cap:.1f}"
                    f"（{(listed - cap) / cap:+.1%}），存在重复计入或非资产科目混入")
            return ({"applicable": False, "note": note, "alerts": [*alerts, note]},
                    f"清算法不适用：{note}")
        if cap and listed < cap * 0.95:
            alerts.append(f"清算清单合计 {listed:.1f} 低于资产总计 {cap:.1f}"
                          f"（{(listed - cap) / cap:+.1%}），清单缺项，清算价值偏低")
        with tag("accounting:liquidation"):
            try:
                reply = self.assess(
                    digest=f"最新年报资产科目账面值（程序已滤除非资产科目，{unit}）：\n{digest}",
                    config=DETERMINISTIC | {"max_tokens": 2500}).result
            except Exception as e:  # 判不适用，不阻塞管线
                logger.warning("清算折扣评估失败（%s）", e)
                reply = LiquidationResult(applicable=False, note=f"评估调用失败：{e}")
        if not reply.applicable:
            note = reply.note or "模型判断清算法不适用"
            return ({"applicable": False, "note": note, "alerts": alerts},
                    f"清算法不适用：{note}")

        lines = ["| 项目 | 账面金额 | 保守变现比例 | 保守价值 | 折扣依据 |",
                 "|---|---:|---:|---:|---|"]
        total = 0.0
        used: list[dict] = []
        covered: set[str] = set()
        for h in reply.haircuts:
            if h.label not in book:
                alerts.append(f"清算折扣科目不在清单内，已跳过：{h.label}")
                continue
            ratio = h.ratio / 100 if h.ratio > 1 else h.ratio  # 容忍把 0.6 写成 60
            if not 0.0 <= ratio <= 1.0:
                alerts.append(f"清算折扣率越界（{h.label} = {h.ratio}），按未评估处理")
                continue
            key, bv = book[h.label]
            covered.add(h.label)
            total += bv * ratio
            used.append({"label": h.label, "concept": key, "book_value": bv,
                         "ratio": ratio, "rationale": h.rationale})
            lines.append(f"| {h.label} | {bv:.1f} | {ratio:.0%} | {bv * ratio:.1f} | {h.rationale} |")
        if not used:
            note = "模型未给出任何可用折扣率，清算价值无法计算"
            return ({"applicable": False, "note": note, "alerts": alerts},
                    f"清算法不适用：{note}")
        if missing := [label for label, _v, _k in rows if label not in covered]:
            alerts.append(f"清算清单 {len(rows)} 项中 {len(missing)} 项未给折扣，按 0 变现"
                          f"计入（清算价值偏低）：" + "、".join(missing[:6]))
        lines.append(f"| 总负债 | {liab:.1f} | 100% | {liab:.1f} | 全额，不打折 |")
        liquidation_value = round(total - liab, 1)
        lines.append(f"| **清算价值** | | | **{liquidation_value:.1f}** | |")
        return {"applicable": True, "line_items": used, "liabilities": liab,
                "liquidation_value": liquidation_value, "note": reply.note,
                "alerts": alerts}, "\n".join(lines)


def _average(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    return (a + b) / 2


def owner_earnings_check(values: dict[str, dict[str, float | None]],
                         years: list[str], window: int = 5) -> dict:
    """盈利质量对账：多年累计/年均的净利润、经营现金流、资本开支、自由现金流及缺口。

    单年自由现金流受折旧摊销加回与营运资金波动影响大，多年均值更接近
    "每年真正能拿出来回报股东的钱"，用于约束 DCF 正常化股东盈余的取值区间。
    """
    ys = years[-window:]

    def series(metric: str) -> dict[str, float | None]:
        return {y: values.get(metric, {}).get(y) for y in ys}

    np_s, ocf_s, cap_s = (series(m) for m in
                          ("net_profit", "operating_cash_flow", "capex"))
    fcf_s = {y: (o - c) if (o := ocf_s[y]) is not None and (c := cap_s[y]) is not None
             else None for y in ys}

    def cum_avg(s: dict[str, float | None]) -> tuple[float | None, float | None]:
        vals = [v for v in s.values() if v is not None]
        if not vals:
            return None, None
        return round(sum(vals), 2), round(sum(vals) / len(vals), 2)

    cum_np, avg_np = cum_avg(np_s)
    cum_ocf, _ = cum_avg(ocf_s)
    cum_cap, _ = cum_avg(cap_s)
    cum_fcf, avg_fcf = cum_avg(fcf_s)
    gap = {y: round(o - n, 2) for y in ys
           if (o := ocf_s[y]) is not None and (n := np_s[y]) is not None}
    latest_fcf = next((fcf_s[y] for y in reversed(ys) if fcf_s[y] is not None), None)

    # 权益交叉验证：窗口期 Δ净资产 vs 同期累计净利润，缺口主要是分红/回购/增发
    eq = values.get("total_equity", {})
    eq_years = [y for y in years if eq.get(y) is not None]
    eq_from = eq_to = ""
    eq_delta: float | None = None
    eq_cum_np: float | None = None
    if len(eq_years) >= 2:
        end, start = eq_years[-1], eq_years[max(0, len(eq_years) - window - 1)]
        eq_end, eq_start = eq[end], eq[start]
        span_np = [v for y in years if start < y <= end
                   if (v := values.get("net_profit", {}).get(y)) is not None]
        if span_np and start != end and eq_end is not None and eq_start is not None:
            eq_from, eq_to = start, end
            eq_delta = round(eq_end - eq_start, 2)
            eq_cum_np = round(sum(span_np), 2)
    equity_check = (
        {"from": eq_from, "to": eq_to, "equity_delta": eq_delta,
         "cum_net_profit": eq_cum_np, "gap": round(eq_cum_np - eq_delta, 2)}
        if eq_delta is not None and eq_cum_np is not None else None
    )

    def fmt(v: float | None) -> str:
        return "—" if v is None else f"{v:.1f}"

    lines = [
        f"盈利质量对账（近{len(ys)}年，程序计算，单位同锚表）：",
        "",
        "| 口径 | 累计 | 年均 |",
        "|---|---:|---:|",
        f"| 净利润 | {fmt(cum_np)} | {fmt(avg_np)} |",
        f"| 经营现金流 | {fmt(cum_ocf)} | — |",
        f"| 资本开支 | {fmt(cum_cap)} | — |",
        f"| 自由现金流 | {fmt(cum_fcf)} | {fmt(avg_fcf)} |",
    ]
    if gap:
        lines += ["",
                  "经营现金流 − 净利润 逐年缺口（折旧摊销加回 + 营运资金变动等）："
                  + "，".join(f"{y}年 {v:+.1f}" for y, v in gap.items())
                  + "。缺口持续大额为正 = 占用上下游资金或大额折旧加回，不可永续，"
                    "不得全额计入股东盈余。"]
    if eq_delta is not None and eq_cum_np is not None:
        lines += ["",
                  (f"净资产交叉验证：{eq_from}→{eq_to} 净资产增加 {fmt(eq_delta)}，"
                   f"同期累计净利润 {fmt(eq_cum_np)}，缺口 {fmt(round(eq_cum_np - eq_delta, 2))}"
                   "（分红/回购/增发等，方向性提示）。")]
    lines += ["",
              (f"→ 正常化股东盈余（DCF 起点）参考区间：[年均净利润 {fmt(avg_np)}，"
               f"年均自由现金流 {fmt(avg_fcf)}]；保守情景不得高于年均自由现金流，"
               f"任何情景不得直接以单年（最新一年 {fmt(latest_fcf)}）自由现金流作起点。")]
    return {
        "window_years": ys,
        "cum": {"net_profit": cum_np, "operating_cash_flow": cum_ocf,
                "capex": cum_cap, "free_cash_flow": cum_fcf},
        "avg": {"net_profit": avg_np, "free_cash_flow": avg_fcf},
        "ocf_np_gap": gap,
        "equity_check": equity_check,
        "latest_fcf": latest_fcf,
        "markdown": "\n".join(lines),
    }


def build_financials(merged_raw: Mapping[str, Mapping[str, float | None]],
                     currency: str | None = None,
                     table_tail: int | None = None) -> dict:
    """{metric: {year: 亿}} → 锚表结构：years/values/比率/markdown/owner_earnings。

    table_tail 限制 markdown 表格只展示最近 N 年（比率/对账仍用全序列计算）。
    """
    years = sorted({y for series in merged_raw.values() for y in series})
    values: dict[str, dict[str, float | None]] = {
        metric: {year: series.get(year) for year in years}
        for metric, series in merged_raw.items()
    }

    ratios: dict[str, dict[str, float | None]] = {
        "gross_margin": {},
        "roe": {},
        "roic_approx": {},
    }
    for i, year in enumerate(years):
        rev = values.get("revenue", {}).get(year)
        gp = values.get("gross_profit", {}).get(year)
        if rev is not None and rev != 0 and gp is not None:
            margin = gp / rev
            # 毛利 > 收入违反会计恒等式，必有一方量级错：置空（不猜哪边）
            ratios["gross_margin"][year] = margin if margin <= 1 else None
        else:
            # 回退：只披露毛利率百分比时容忍 0.195 / 19.5 两种写法
            gm = values.get("gross_margin", {}).get(year)
            ratios["gross_margin"][year] = (
                (gm / 100 if gm > 1.5 else gm) if gm is not None else None
            )

        net = values.get("net_profit", {}).get(year)
        eq = values.get("total_equity", {}).get(year)
        prev_eq = values.get("total_equity", {}).get(years[i - 1]) if i > 0 else None
        avg_eq = _average(eq, prev_eq)
        if net is not None and avg_eq is not None and avg_eq != 0:
            ratios["roe"][year] = net / avg_eq
        else:
            ratios["roe"][year] = None

        pbt = values.get("pre_tax_profit", {}).get(year)
        ta = values.get("total_assets", {}).get(year)
        cl = values.get("current_liabilities", {}).get(year)
        invested = (ta - cl) if ta is not None and cl is not None else None
        prev_inv = None
        if i > 0:
            ta_p = values.get("total_assets", {}).get(years[i - 1])
            cl_p = values.get("current_liabilities", {}).get(years[i - 1])
            if ta_p is not None and cl_p is not None:
                prev_inv = ta_p - cl_p
        avg_inv = _average(invested, prev_inv)
        if pbt is not None and avg_inv is not None and avg_inv != 0:
            ratios["roic_approx"][year] = pbt / avg_inv
        else:
            ratios["roic_approx"][year] = None

    disp = years[-table_tail:] if table_tail else years
    lines = [
        "| 指标 | " + " | ".join(f"{y}年" for y in disp) + " |",
        "|---|" + "---:|" * len(disp),
    ]
    metric_labels = {
        "revenue": "营业收入",
        "gross_profit": "毛利",
        "gross_margin": "毛利率",
        "operating_profit": "经营利润",
        "pre_tax_profit": "除所得税前利润",
        "net_profit": "净利润",
        "total_assets": "资产总值",
        "total_liabilities": "负债总额",
        "total_equity": "权益总额",
        "invested_capital": "投入资本",
        "operating_cash_flow": "经营活动现金流",
        "capex": "资本开支",
        "cash_and_equivalents": "现金及等价物",
        "interest_bearing_debt": "有息负债合计",
        "roe": "ROE（%）",
        "roic_approx": "ROIC近似（%）",
    }
    invested_capital: dict[str, float | None] = {}
    for y in years:
        ta = values.get("total_assets", {}).get(y)
        cl = values.get("current_liabilities", {}).get(y)
        invested_capital[y] = (ta - cl) if ta is not None and cl is not None else None

    for key, label in metric_labels.items():
        row_vals = []
        if key == "invested_capital":
            src = invested_capital
        else:
            src = ratios.get(key) if key in ratios else values.get(key)
        if src is None:
            continue
        for y in disp:
            v = src.get(y)
            if v is None:
                row_vals.append("—")
            elif key in ("gross_margin", "roe", "roic_approx"):
                row_vals.append(f"{v * 100:.1f}%")
            else:
                row_vals.append(f"{v:.1f}")
        lines.append(f"| {label} | " + " | ".join(row_vals) + " |")

    return {
        "years": years,
        "values": values,
        "ratios": ratios,
        "currency": currency or "",
        "markdown": "\n".join(lines),
        "owner_earnings": owner_earnings_check(values, years),
    }


def anchor_text(data: dict) -> str:
    """build_financials 结果 → 可放进 prompt 的锚表文本。"""
    if not data["years"]:
        return "（未解析到财务摘要表）"
    unit = f"亿{data['currency']}" if data.get("currency") else "亿（币种未标注）"
    return (
        "以下财务数据已从各年报的财务摘要中自动提取并合并，"
        f"单位为报表主币{unit}，后公告覆盖前公告。\n\n"
        + data["markdown"]
        + "\n\n"
        "请以上表为财务数字的基准，把笔记中的经营现金流、资本开支、自由现金流、"
        "有息负债等数据按年份补齐；表内毛利率/ROE/ROIC 是程序按锚表数字的计算值，"
        "必须直接采用（即使笔记说'未直接披露'，那是披露口径问题，不是数据缺失）。"
        "\n\n" + data["owner_earnings"]["markdown"]
    )
