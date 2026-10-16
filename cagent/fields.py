"""fields：字段契约与读值（纯函数，无 LLM、无 IO）

两层字段边界里的第一层：固定字段——准则强制列报、且挂着判据（进恒等式，或供周转/杠杆/对账用）。
只有这一层参与算术与跨公司比较。公司特有的明细不做字段，由按需明细清单承载（见 checks/facts）。

同名字段的读取规则：GROUP_ROLES 相加（调节项这类成组行）；其余只认第一次出现的行——合计行在明细行之前。

用法：
    from cagent.fields import ROLES_BY_KIND, UNIT_SCALE, number, roles_from_rows, duplicate_roles
"""

# role → 中文释义。抽取层按 kind 分组喂给模型，模型据此标注行项目。
# 只保留固定字段：准则强制列报、且挂着判据（进恒等式、或供周转率/杠杆/对账用）。
# 公司特有的明细不做字段，重构后由"按需明细清单 + 逐字校验"承载。
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
    "other_receivables": "其他应收款（大股东占款一类冗余资产的信号）",
    "inventory": "存货",
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

NOTES_ROLES = {
    "ar_gross": "应收账款账面余额（账龄分析表的合计，计提坏账准备之前的数）",
    "ar_provision": "应收账款坏账准备合计（按组合计提与按单项计提之和）",
    "ar_aging_over_2y": "应收账款账龄两年以上的账面余额（原表按年限披露时填；只按信用期与逾期披露则留空）",
    "inventory_gross": "存货账面余额（计提跌价准备之前的数）",
    "inventory_provision": "存货跌价准备期末余额",
    "restricted_cash": "使用受到限制的货币资金",
}

ROLES_BY_KIND: dict[str, dict[str, str]] = {
    "balance": BALANCE_ROLES,
    "income": INCOME_ROLES,
    "cashflow": CASHFLOW_ROLES,
    "supplement": SUPPLEMENT_ROLES,
    "equity": EQUITY_ROLES,
    "notes": NOTES_ROLES,
}

DA_ROLES: tuple[str, ...] = ("da_fixed", "da_rou", "da_intangible", "da_lt_deferred", "da_inv_property")

ROLES: dict[str, str] = {role: desc for kind_roles in ROLES_BY_KIND.values() for role, desc in kind_roles.items()}

# 成组相加的 role（补充资料调节项这类一行不说明问题的科目）；其余 role 一行一义，重复标注取第一行（合计行在明细行前）。
GROUP_ROLES: frozenset[str] = frozenset({"supplement_other"})

def unit_scale(unit: str) -> float:
    """原表单位 → 倍数。先把写法归一再判，别指望枚举得完。

    年报里同一件事有无数写法：千美元 / US$'000 / US$' 000 / 千港元 / HK$'000 / 人民币千元 / 百万元。
    按"币种 + 量级"归一到倍数，认不出量级就按 1（元）算。
    """
    text = (unit or "").strip().lower().replace(" ", "").replace("'", "").replace("’", "").replace("（", "(").replace("）", ")")
    if text in UNIT_SCALE:
        return UNIT_SCALE[text]
    if any(token in text for token in ("百万", "million", "m)", "mn")):
        return 1e6
    if any(token in text for token in ("千", "000", "thousand", "k)")):
        return 1e3
    if "万" in text:
        return 1e4
    return 1.0


def unit_currency(unit: str) -> str:
    """原表单位 → 币种（元/港元/美元）。只认币种，不做汇率换算。"""
    text = (unit or "").lower().replace(" ", "")
    if any(token in text for token in ("港元", "港币", "hk$", "hkd")):
        return "港元"
    if any(token in text for token in ("美元", "us$", "usd")):
        return "美元"
    return "元"


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
    "千美元": 1e3,
    "美元千元": 1e3,
    "港币千元": 1e3,
    "us$000": 1e3,
    "usd000": 1e3,
    "hkd000": 1e3,
}

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
    scale = unit_scale(unit)
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


