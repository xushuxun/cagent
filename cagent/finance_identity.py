"""会计恒等式校验：纯函数，无 LLM、无 IO。

数字由模型从年报原表提取，提得对不对由会计恒等式校验——course.md 第十点：资产＝负债＋权益，
一百多个科目锁在一个等式里，动一个必然逼着动别的。恒等式不成立只说明这份数字不可用或需复议，
不是「公司做假账」的结论；反过来，等式成立也只保锚点行，明细行需自行承担风险。

两条已知边界，别指望恒等式包办：
    母公司报表自身也满足全部恒等式，口径抄错它抓不住——那道关在抽取层（consolidated 字段）；
    会计政策口径（合并范围、重述、非经常性损益归类）变了，等式照样平——那是分析层要讲的。

role 词表是本模块与抽取层的接口：抽取层按它标注科目，校验层按它列式。
同名 role：GROUP_ROLES 相加（调节项这类成组行），其余只认第一次出现的行（合计行在明细行之前）；
Identity.optional 的行原表可能没有，缺了按 0 计（有行却漏标，等式照样不平）。

用法：
    from cagent.finance_identity import ROLES_BY_KIND, UNIT_SCALE, check_report, roles_from_rows, rollforward_check, verify
"""

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Identity:
    """一条恒等式：terms 各项加权求和应等于 0；soft 只报告差异，不触发重取。"""

    id: str
    desc: str
    terms: tuple[tuple[str, float], ...]
    soft: bool = False
    optional: tuple[str, ...] = ()   # 原表可能没有的行：缺了按 0 计


@dataclass(frozen=True)
class Check:
    id: str
    desc: str
    status: str  # pass | fail | skip | diff
    delta: float | None = None
    missing: tuple[str, ...] = field(default=())


# role → 中文释义。抽取层按 kind 分组喂给模型，模型据此标注行项目。
# 分两类：锚点行进恒等式、有担保；明细行无恒等式担保，供分析层（周转率、冗余资产、低报资产）取用。
BALANCE_ROLES = {
    "total_current_assets": "流动资产合计",
    "total_noncurrent_assets": "非流动资产合计",
    "total_assets": "资产总计",
    "total_current_liabilities": "流动负债合计",
    "total_noncurrent_liabilities": "非流动负债合计",
    "total_liabilities": "负债合计",
    "equity_parent": "归属于母公司股东权益合计（归母所有者权益合计）",
    "minority_equity": "少数股东权益",
    "total_equity": "所有者权益合计（股东权益合计）",
    "monetary_funds": "货币资金",
    "accounts_receivable": "应收账款",
    "notes_receivable": "应收票据",
    "receivables_financing": "应收款项融资",
    "other_receivables": "其他应收款（大股东占款一类冗余资产的信号）",
    "prepayments": "预付款项",
    "inventory": "存货",
    "fixed_assets": "固定资产",
    "construction_in_progress": "在建工程",
    "right_of_use_assets": "使用权资产",
    "intangible_assets": "无形资产",
    "development_exp": "开发支出（研发资本化形成的在研项目）",
    "goodwill": "商誉",
    "investment_property": "投资性房地产（低报资产的常见藏身处）",
    "accounts_payable": "应付账款",
    "notes_payable": "应付票据",
    "contract_assets": "合同资产",
    "contract_liabilities": "合同负债",
    "short_term_borrowings": "短期借款",
    "long_term_borrowings": "长期借款",
    "bonds_payable": "应付债券",
    "lease_liabilities": "租赁负债",
}

INCOME_ROLES = {
    "revenue": "营业总收入（营业收入）",
    "cogs_total": "营业总成本",
    "cogs": "营业成本",
    "taxes_surcharges": "税金及附加",
    "selling_exp": "销售费用",
    "admin_exp": "管理费用",
    "rd_exp": "研发费用",
    "fin_exp": "财务费用（净收益为负）",
    "interest_expense": "财务费用中的利息费用（其中项）",
    "other_income": "其他收益",
    "invest_income": "投资收益",
    "fv_change_income": "公允价值变动收益",
    "credit_impairment": "信用减值损失（损失为负）",
    "asset_impairment": "资产减值损失（损失为负）",
    "asset_disposal": "资产处置收益",
    "operating_profit": "营业利润",
    "nonop_income": "营业外收入",
    "nonop_expense": "营业外支出",
    "total_profit": "利润总额",
    "income_tax": "所得税费用",
    "net_profit": "净利润",
    "net_profit_parent": "归属于母公司股东的净利润",
    "minority_profit": "少数股东损益",
    "oci": "其他综合收益的税后净额",
    "total_comprehensive_income": "综合收益总额",
}

CASHFLOW_ROLES = {
    "ocf_inflow": "经营活动现金流入小计",
    "ocf_outflow": "经营活动现金流出小计",
    "ocf": "经营活动产生的现金流量净额",
    "icf_inflow": "投资活动现金流入小计",
    "icf_outflow": "投资活动现金流出小计",
    "icf": "投资活动产生的现金流量净额",
    "fcf_inflow": "筹资活动现金流入小计",
    "fcf_outflow": "筹资活动现金流出小计",
    "fcf": "筹资活动产生的现金流量净额",
    "fx_effect": "汇率变动对现金及现金等价物的影响",
    "cash_net_increase": "现金及现金等价物净增加额（净减少额为负）",
    "cash_begin": "期初（年初）现金及现金等价物余额",
    "cash_end": "期末（年末）现金及现金等价物余额",
    "capex": "购建固定资产、无形资产和其他长期资产支付的现金",
    "dividend_paid": "分配股利、利润或偿付利息支付的现金",
    "cash_from_sales": "销售商品、提供劳务收到的现金",
}

SUPPLEMENT_ROLES = {
    "net_profit_supplement": "补充资料「1.将净利润调节为经营活动现金流量」一节开头的净利润",
    "da_fixed": "固定资产折旧",
    "da_rou": "使用权资产折旧",
    "da_intangible": "无形资产摊销",
    "da_lt_deferred": "长期待摊费用摊销",
    "da_inv_property": "投资性房地产折旧及摊销（原表没有此行时留空）",
    "supplement_other": "「1.将净利润调节为经营活动现金流量」一节里除净利润、折旧摊销五行外的其余调节项（减值准备、公允价值变动、投资收益、财务费用、递延所得税、存货、经营性应收应付、其他；每行都标此 role，成组相加）。该节之后的小节（不涉及现金收支、现金及现金等价物净变动）整节都不标 role",
    "ocf_supplement": "补充资料「1.将净利润调节为经营活动现金流量」一节末尾的经营活动产生的现金流量净额",
}

EQUITY_ROLES = {
    "dividend": "对股东的分配（利润分配下该行，未分配利润列的绝对值）",
    "capital_raised": "所有者投入和减少资本（该行的归母权益小计列；无小计列时取股东权益合计列）",
}

ROLES_BY_KIND: dict[str, dict[str, str]] = {
    "balance": BALANCE_ROLES,
    "income": INCOME_ROLES,
    "cashflow": CASHFLOW_ROLES,
    "supplement": SUPPLEMENT_ROLES,
    "equity": EQUITY_ROLES,
}

DA_ROLES: tuple[str, ...] = ("da_fixed", "da_rou", "da_intangible", "da_lt_deferred", "da_inv_property")

ROLES: dict[str, str] = {role: desc for kind_roles in ROLES_BY_KIND.values() for role, desc in kind_roles.items()}

# 成组相加的 role（补充资料调节项这类一行不说明问题的科目）；其余 role 一行一义，重复标注取第一行（合计行在明细行前）。
GROUP_ROLES: frozenset[str] = frozenset({"supplement_other"})

UNIT_SCALE: dict[str, float] = {
    "元": 1.0,
    "人民币元": 1.0,
    "千元": 1e3,
    "千人民币元": 1e3,
    "千港元": 1e3,
    "万元": 1e4,
    "百万元": 1e6,
    "人民币百万元": 1e6,
    "人民币千元": 1e3,
}

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

_NULL_CELLS = frozenset({"", "-", "--", "—", "－", "/", "不适用", "无", "nil", "n/a"})


def number(cell: str | float | None) -> float | None:
    """单元格 → 数值：千分位去逗号，括号负数取负，空白与「-」为 None。"""
    if cell is None:
        return None
    if isinstance(cell, (int, float)):
        return float(cell)
    text = str(cell).strip().replace(",", "").replace("，", "")
    if text.lower() in _NULL_CELLS:
        return None
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    try:
        return float(text)
    except ValueError:
        return None


def roles_from_rows(rows: list[dict], unit: str = "元", column: int = 0) -> dict[str, float]:
    """抽取产物里的行 → {role: 值(元)}；无 role 或该列无数值的行跳过。

    只取第 column 列（默认本期列），上期列留给多年序列层按需自行取用。
    GROUP_ROLES 相加（调节项）；其余 role 只认第一次出现的行——「一、营业总收入」在「其中:营业收入」之前，
    取第一行才不会被合计行与明细行重复累加。重复标注用 duplicate_roles 查出来告警。
    """
    scale = UNIT_SCALE.get(unit.strip(), 1.0)
    roles: dict[str, float] = {}
    for row in rows:
        role = (row.get("role") or "").strip()
        if role not in ROLES:
            continue
        values = row.get("values") or []
        if column >= len(values):
            continue
        value = number(values[column])
        if value is None:
            continue
        if role in roles and role not in GROUP_ROLES:
            continue
        roles[role] = roles.get(role, 0.0) + value * scale
    return roles


def duplicate_roles(rows: list[dict]) -> dict[str, list[str]]:
    """重复标注了同一 role 的行（合计行与其中明细行），返回 {role: [行标签]}，供抽取层告警。"""
    seen: dict[str, list[str]] = {}
    for row in rows:
        role = (row.get("role") or "").strip()
        if role in ROLES:
            seen.setdefault(role, []).append(str(row.get("label") or ""))
    return {role: labels for role, labels in seen.items() if len(labels) > 1 and role not in GROUP_ROLES}


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
        passed = abs(delta) <= max(abs_tol, rel_tol * scale)
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
