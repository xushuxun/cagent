"""checks：校验层（纯函数，无 LLM、无 IO）

数字由模型从年报原表提取，提得对不对由三类校验兜着：

    恒等式      勾稽是否成立——course.md 第十点：资产＝负债＋权益，一百多个科目锁在一个等式里，
                动一个必然逼着动别的。等式成立只保锚点行，明细行需自行承担风险。
    逐字        每个数必须在年报原文里逐字出现：恒等式保勾稽，保不了"这个数是不是报表里的"，
                模型完全可以编一个让等式成立的数（实测出现过）。
    页码        每个值都要带得出页码，缺了就不能被引用。

两条已知边界，别指望校验包办：母公司报表自身也满足全部恒等式，口径抄错它抓不住（那道关在抽取层）；
会计政策口径（合并范围、重述、非经常性损益归类）变了，等式照样平（那是分析层要讲的）。

用法：
    from cagent.checks import IDENTITIES, check_report, rollforward_check, verbatim_check, verify
"""

from collections.abc import Mapping
from dataclasses import dataclass, field

from cagent.fields import DA_ROLES


@dataclass(frozen=True)
class Identity:
    """一条恒等式：terms 各项加权求和应等于 0；soft 只报告差异，不触发重取。"""

    id: str
    desc: str
    terms: tuple[tuple[str, float], ...]
    soft: bool = False
    optional: tuple[str, ...] = ()   # 原表可能没有的行：缺了按 0 计
    sense: str = "="                 # "=" 加权和应为 0；"<=" 加权和应不大于 0（如 跌价准备 − 存货 ≤ 0）


@dataclass(frozen=True)
class Check:
    """一条校验结论。status 取 pass / fail / skip / diff。"""

    id: str
    desc: str
    status: str
    delta: float | None = None
    missing: tuple[str, ...] = field(default=())


IDENTITIES: tuple[Identity, ...] = (
    # 资产负债表
    Identity("balance.equation", "资产总计 = 负债合计 + 所有者权益合计", (("total_assets", 1.0), ("total_liabilities", -1.0), ("total_equity", -1.0))),
    Identity("balance.equity_split", "所有者权益合计 = 归母权益 + 少数股东权益", (("total_equity", 1.0), ("equity_parent", -1.0), ("minority_equity", -1.0))),
    Identity("balance.assets_split", "资产总计 = 流动资产合计 + 非流动资产合计", (("total_assets", 1.0), ("total_current_assets", -1.0), ("total_noncurrent_assets", -1.0))),
    Identity(
        "balance.liabilities_split",
        "负债合计 = 流动负债合计 + 非流动负债合计",
        (("total_liabilities", 1.0), ("total_current_liabilities", -1.0), ("total_noncurrent_liabilities", -1.0)),
    ),
    # 利润表
    Identity(
        "income.cogs_total",
        "营业总成本 = 营业成本 + 税金及附加 + 销售费用 + 管理费用 + 研发费用 + 财务费用",
        (("cogs_total", 1.0),) + tuple((role, -1.0) for role in ("cogs", "taxes_surcharges", "selling_exp", "admin_exp", "rd_exp", "fin_exp")),
    ),
    Identity(
        "income.operating_profit",
        "营业利润 = 营业总收入 − 营业总成本 + 其他收益 + 投资收益 + 公允价值变动 + 信用减值 + 资产减值 + 资产处置收益",
        (("operating_profit", 1.0), ("revenue", -1.0), ("cogs_total", 1.0))
        + tuple((role, -1.0) for role in ("other_income", "invest_income", "fv_change_income", "credit_impairment", "asset_impairment", "asset_disposal")),
    ),
    Identity("income.total_profit", "利润总额 = 营业利润 + 营业外收入 − 营业外支出", (("total_profit", 1.0), ("operating_profit", -1.0), ("nonop_income", -1.0), ("nonop_expense", 1.0))),
    Identity("income.net_profit", "净利润 = 利润总额 − 所得税费用", (("net_profit", 1.0), ("total_profit", -1.0), ("income_tax", 1.0))),
    Identity("income.parent_split", "净利润 = 归母净利润 + 少数股东损益", (("net_profit", 1.0), ("net_profit_parent", -1.0), ("minority_profit", -1.0))),
    Identity("income.comprehensive", "综合收益总额 = 净利润 + 其他综合收益的税后净额", (("total_comprehensive_income", 1.0), ("net_profit", -1.0), ("oci", -1.0))),
    # 现金流量表
    Identity("cashflow.ocf", "经营活动现金流量净额 = 流入小计 − 流出小计", (("ocf", 1.0), ("ocf_inflow", -1.0), ("ocf_outflow", 1.0))),
    Identity("cashflow.icf", "投资活动现金流量净额 = 流入小计 − 流出小计", (("icf", 1.0), ("icf_inflow", -1.0), ("icf_outflow", 1.0))),
    Identity("cashflow.fcf", "筹资活动现金流量净额 = 流入小计 − 流出小计", (("fcf", 1.0), ("fcf_inflow", -1.0), ("fcf_outflow", 1.0))),
    Identity(
        "cashflow.net_increase",
        "现金净增加额 = 经营净额 + 投资净额 + 筹资净额 + 汇率影响",
        (("cash_net_increase", 1.0), ("ocf", -1.0), ("icf", -1.0), ("fcf", -1.0), ("fx_effect", -1.0)),
    ),
    Identity("cashflow.balance", "期末现金及现金等价物 = 期初 + 净增加额", (("cash_end", 1.0), ("cash_begin", -1.0), ("cash_net_increase", -1.0))),
    # 补充资料：一次性给折旧摊销那组行上保险
    Identity(
        "supplement.reconcile",
        "补充资料：净利润 + 折旧摊销 + Σ其余调节项 = 经营活动现金流量净额",
        (("net_profit_supplement", 1.0),) + tuple((role, 1.0) for role in DA_ROLES) + (("supplement_other", 1.0), ("ocf_supplement", -1.0)),
        optional=DA_ROLES,
    ),
    # 附注：能跟主表对上的就硬校验，对不上的只用不等式兜底
    Identity("notes.ar_provision", "应收账款账面余额 − 坏账准备 = 资产负债表应收账款", (("ar_gross", 1.0), ("ar_provision", -1.0), ("accounts_receivable", -1.0))),
    Identity("notes.ar_aging_over_2y", "账龄两年以上应收 ≤ 应收账款账面余额", (("ar_aging_over_2y", 1.0), ("ar_gross", -1.0)), sense="<="),
    Identity("notes.inventory_pair", "存货账面余额 − 存货跌价准备 = 资产负债表存货", (("inventory_gross", 1.0), ("inventory_provision", -1.0), ("inventory", -1.0))),
    Identity("notes.restricted_cash", "受限货币资金 ≤ 货币资金", (("restricted_cash", 1.0), ("monetary_funds", -1.0)), sense="<="),
    # 跨表
    Identity("cross.ocf", "补充资料经营净额 = 现金流量表经营净额", (("ocf_supplement", 1.0), ("ocf", -1.0))),
    Identity("cross.net_profit", "补充资料净利润 = 利润表净利润", (("net_profit_supplement", 1.0), ("net_profit", -1.0))),
    Identity(
        "cross.cash_equivalents",
        "期末现金及现金等价物 ≈ 货币资金（差额为受限资金与三个月以上定期存款）",
        (("cash_end", 1.0), ("monetary_funds", -1.0)),
        soft=True,
    ),
)

def verify(roles: Mapping[str, float], *, abs_tol: float = 0.5, rel_tol: float = 1e-12) -> list[Check]:
    """逐条校验恒等式。缺 role 记 skip，差值超容差记 fail（soft 恒等式记 diff）。

    容差只为吸收浮点求和误差（±0.5 元）；账目对不上就是提数错了，不靠放宽容差糊过去。
    """
    checks: list[Check] = []
    for identity in IDENTITIES:
        missing = tuple(role for role, _ in identity.terms if roles.get(role) is None and role not in identity.optional)
        if missing:
            checks.append(Check(identity.id, identity.desc, "skip", None, missing))
            continue
        delta = sum(sign * float(roles.get(role) or 0.0) for role, sign in identity.terms)
        scale = max(abs(float(roles.get(role) or 0.0)) for role, _ in identity.terms) or 1.0
        tolerance = max(abs_tol, rel_tol * scale)
        passed = delta <= tolerance if identity.sense == "<=" else abs(delta) <= tolerance
        checks.append(Check(identity.id, identity.desc, ("pass" if passed else ("diff" if identity.soft else "fail")), delta))
    return checks


def rollforward_check(roles: Mapping[str, float], roles_prior: Mapping[str, float], *, abs_tol: float = 0.5) -> Check:
    """归母权益滚动（要两期口径，故不走 verify）：期末 = 期初 + 归母净利 + 股东投入 − 分红 + 其他。

    残差就是其他综合收益与其他权益变动，恒不为零也正常，故只报 diff 不作 fail；
    分母（期初归母权益）缺失则 skip。
    """
    need = ("equity_parent", "net_profit_parent", "capital_raised", "dividend")
    missing = tuple(role for role in need if roles.get(role) is None) + (() if roles_prior.get("equity_parent") is not None else ("equity_parent(上期)",))
    desc = "归母权益期末 ≈ 期初 + 归母净利润 + 股东投入 − 分红（残差为其他权益变动）"
    if missing:
        return Check("equity.rollforward", desc, "skip", None, missing)
    delta = (
        float(roles["equity_parent"])
        - float(roles_prior["equity_parent"])
        - float(roles["net_profit_parent"])
        - float(roles["capital_raised"])
        + float(roles["dividend"])
    )
    scale = abs(float(roles["equity_parent"])) or 1.0
    return Check("equity.rollforward", desc, "pass" if abs(delta) <= abs_tol * scale * 1e-9 + abs_tol else "diff", delta)


def check_report(checks: list[Check], *, prefix: str = "") -> str:
    """一行一条的校验摘要，供日志与 benchmark 输出。"""
    lines = []
    for check in checks:
        if prefix and not check.id.startswith(prefix):
            continue
        tail = ""
        if check.status == "skip":
            tail = f" 缺 {','.join(check.missing)}"
        elif check.delta is not None:
            tail = f" Δ={check.delta:,.2f}"
        lines.append(f"[{check.status}] {check.id}{tail}")
    return "\n".join(lines)


def _number_forms(value: float) -> list[str]:
    """一个数在年报里可能的写法：正负两种、不同小数位、千元/万元/亿元等单位的换算写法。

    年报里同一个数可能写作「(1,234.5)」（括号表负）、「1.12亿元」或「5,037,603 千元」，
    只按原样匹配会误报——负数必须同时给出绝对值形态。
    """
    forms: set[str] = set()
    for base in (value, -value):
        for scaled in (base, base / 1e3, base * 1e3, base / 1e4, base * 1e4, base / 1e8, base * 1e8):
            for text in (f"{scaled:,.2f}", f"{scaled:,.0f}", f"{scaled:f}".rstrip("0").rstrip("."), f"{scaled:,.6f}".rstrip("0").rstrip(".")):
                cleaned = text.replace(",", "")
                if cleaned and cleaned not in {"0", "-0"}:
                    forms.add(cleaned)
    return sorted(forms)


def verbatim_check(kind: str, table: dict, report_text: str) -> Check:
    """数字必须在年报原文里逐字出现。value 太小的不作为证据（全文里易巧合命中）。"""
    squashed = report_text.replace(",", "").replace("，", "")
    missing = []
    for row in table.get("rows") or []:
        for value in row.get("values") or []:
            if value is None or abs(float(value)) < 1000:
                continue
            if not any(form in squashed for form in _number_forms(float(value))):
                missing.append(f'{row.get("role") or row.get("label")}={float(value):,.2f}')
                break
    return Check(f"verbatim.{kind}", "每个数都要在年报原文里逐字出现", "pass" if not missing else "fail", None, tuple(missing[:5]))
