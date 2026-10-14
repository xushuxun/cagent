"""finance_metrics 模块：资本回报指标纯函数计算层

从 finance_extract 产物 financials.json 的多年序列计算指标。口径遵循 docs/prd.md
「资本回报的设计原则」：整体股东口径（归母），不做每股折算；回报率看增量资本回报，
利润看所有者收益。全部程序直解，无 LLM、无判断规则。

单位约定：输入输出一律人民币元（比率为无量纲 float）；展示层自行换算亿元。
"""

import json
from pathlib import Path

_FIN = dict  # 一份年报的 financials.json：{"year", "income", "balance", "cashflow", "supplement", "equity", ...}
_SERIES = dict[int, _FIN]


def _div(a: float | None, b: float | None) -> float | None:
    """安全除法；分母为 None/0 返回 None。"""
    if a is None or not b:
        return None
    return a / b


def _g(fin: _FIN, section: str, field: str) -> float | None:
    return fin.get(section, {}).get(field)


def load_financials(root: Path, market: str, stock: str) -> _SERIES:
    """读取 knowledge/*/financials.json，按报告期年份聚合为 {year: financials}。"""
    series: _SERIES = {}
    for p in sorted((Path(root) / market / stock / "knowledge").glob("*/financials.json")):
        fin = json.loads(p.read_text(encoding="utf-8"))
        series[int(fin["year"])] = fin
    if not series:
        raise SystemExit(f"{Path(root) / market / stock / 'knowledge'} 没有 financials.json，先跑 finance_extract.py")
    return series


def prev_of(series: _SERIES, year: int) -> _FIN | None:
    """上一年（要求年份连续，序列有缺口时返回 None）。"""
    return series.get(year - 1)


def avg(field: str, section: str, fin: _FIN, prev: _FIN | None) -> float | None:
    """(期初+期末)/2；无上年时退回期末数。"""
    cur = _g(fin, section, field)
    old = _g(prev, section, field) if prev else None
    if cur is None:
        return None
    return (cur + old) / 2 if old is not None else cur


def roe(fin: _FIN, prev: _FIN | None) -> float | None:
    """ROE = 归母净利润 ÷ 平均归母净资产。"""
    return _div(_g(fin, "income", "net_profit_parent"), avg("equity_parent", "balance", fin, prev))


def incremental_return(series: _SERIES, year: int, window: int = 3) -> float | None:
    """增量资本回报：窗口内 Δ归母净利润 ÷ Δ归母净资产（PRD:39-40）。

    增长值与资本成本的分界只看这条：再投进去的每一元赚多少。
    """
    base = series.get(year - window)
    fin = series.get(year)
    if not base or not fin:
        return None
    d_np = _g(fin, "income", "net_profit_parent")
    b_np = _g(base, "income", "net_profit_parent")
    d_eq = _g(fin, "balance", "equity_parent")
    b_eq = _g(base, "balance", "equity_parent")
    if d_np is None or b_np is None or d_eq is None or b_eq is None or d_eq == b_eq:
        return None
    return (d_np - b_np) / (d_eq - b_eq)


def operating_wc(fin: _FIN) -> float | None:
    """经营性营运资本 = 应收票据+应收账款+应收款项融资+预付+存货+合同资产 − 应付票据−应付账款−合同负债。

    口径近似：不含应付职工薪酬与应交税费等其他经营性应付。
    """
    b = fin.get("balance", {})
    assets = ["notes_receivable", "accounts_receivable", "receivables_financing", "prepayments", "inventory", "contract_assets"]
    liabs = ["notes_payable", "accounts_payable", "contract_liabilities"]
    a = [b.get(f) for f in assets]
    l = [b.get(f) for f in liabs]
    if any(v is None for v in a + l):
        return None
    return sum(float(v) for v in a) - sum(float(v) for v in l)


def da_total(fin: _FIN) -> float | None:
    """折旧摊销合计：现金流量表补充资料中各行本期数之和（缺失行跳过，全缺返回 None）。"""
    vals = [fin.get("supplement", {}).get(k) for k in ("da_fixed", "da_rou", "da_intangible", "da_lt_deferred", "da_inv_property")]
    vals = [v for v in vals if v is not None]
    return sum(vals) if vals else None


def owner_earnings(fin: _FIN, prev: _FIN | None) -> float | None:
    """所有者收益（巴菲特 1986 口径，PRD:33-34）：
    归母净利润 + 折旧摊销 − 购建长期资产支付的现金 − 经营性营运资本增量。"""
    np_parent = _g(fin, "income", "net_profit_parent")
    da = da_total(fin)
    capex = _g(fin, "cashflow", "capex")
    if np_parent is None or da is None or capex is None:
        return None
    owc, owc_prev = operating_wc(fin), operating_wc(prev) if prev else 0.0
    if owc is None:
        return None
    return np_parent + da - capex - (owc - (owc_prev or 0.0))


def cash_quality(fin: _FIN) -> float | None:
    """净现比 = 经营现金流净额 ÷ 归母净利润（课程「漏斗一」：报了利润收不回现金）。"""
    return _div(_g(fin, "cashflow", "ocf"), _g(fin, "income", "net_profit_parent"))


def pie_split(fin: _FIN) -> dict[str, float | None]:
    """分饼：每 100 元收入中各项成本费用占多少（课程「利润表是分饼图」）。股东（净利润）排最后。"""
    rev = _g(fin, "income", "revenue")
    parts = {
        "成本": _g(fin, "income", "cogs"),
        "税金及附加": _g(fin, "income", "taxes_surcharges"),
        "销售费用": _g(fin, "income", "selling_exp"),
        "管理费用": _g(fin, "income", "admin_exp"),
        "研发费用": _g(fin, "income", "rd_exp"),
        "财务费用": _g(fin, "income", "fin_exp"),
        "其他损益": None,  # 其他收益+投资收益+公允价值变动+减值+处置等净额，程序合并
        "净利润": _g(fin, "income", "net_profit_parent"),
    }
    other = None
    op = _g(fin, "income", "operating_profit")
    listed = [rev, parts["成本"], parts["税金及附加"], parts["销售费用"], parts["管理费用"], parts["研发费用"], parts["财务费用"]]
    nums: list[float] = []
    for v in listed:
        if v is None:
            break
        nums.append(v)
    if op is not None and len(nums) == len(listed):
        # 其他损益 = 营业利润 − (收入 − 成本 − 税金及附加 − 销售 − 管理 − 研发 − 财务费用)
        other = op - (nums[0] - sum(nums[1:]))
    parts["其他损益"] = other
    if not rev:
        return {k: None for k in parts}
    return {k: (v / rev * 100 if v is not None else None) for k, v in parts.items()}


def cash_check(fin: _FIN) -> dict[str, float | None]:
    """现金流体检（课程「现金流量表是测谎仪」）：三项净额占收入比 + 经营投资合计。"""
    rev = _g(fin, "income", "revenue")
    ocf, icf, fcf = _g(fin, "cashflow", "ocf"), _g(fin, "cashflow", "icf"), _g(fin, "cashflow", "financing_cf")
    oi = ocf + icf if ocf is not None and icf is not None else None
    return {
        "经营净额/收入": _div(ocf, rev),
        "投资净额/收入": _div(icf, rev),
        "筹资净额/收入": _div(fcf, rev),
        "经营+投资/收入": _div(oi, rev),
    }


def turnover(fin: _FIN, prev: _FIN | None) -> dict[str, float | None]:
    """周转率：应收、存货、总资产（课程「纵向找突变」的信号源）。"""
    return {
        "应收账款周转率": _div(_g(fin, "income", "revenue"), avg("accounts_receivable", "balance", fin, prev)),
        "存货周转率": _div(_g(fin, "income", "cogs"), avg("inventory", "balance", fin, prev)),
        "总资产周转率": _div(_g(fin, "income", "revenue"), avg("total_assets", "balance", fin, prev)),
    }


def one_yuan_test(series: _SERIES, year: int, window: int = 3) -> float | None:
    """一元检验（PRD:36-37，整体股东口径）：窗口内每留存 1 元利润变成多少归母净资产。

    Δ归母净资产 ÷ Σ(归母净利润 − 分红)。留存利润应当通过一元检验。
    """
    years = [y for y in range(year - window + 1, year + 1) if y in series]
    if len(years) < 2:
        return None
    retained = 0.0
    first, last = series[years[0]], series[years[-1]]
    for y in years:
        fin = series[y]
        np_parent = _g(fin, "income", "net_profit_parent")
        dividend = _g(fin, "equity", "dividend")
        if np_parent is None or dividend is None:
            return None
        retained += np_parent - dividend
    b_eq = _g(first, "balance", "equity_parent")
    d_eq = _g(last, "balance", "equity_parent")
    if b_eq is None or d_eq is None or retained <= 0:
        return None
    return (d_eq - b_eq) / retained


def year_row(series: _SERIES, year: int) -> dict[str, float | None]:
    """一年的全部指标汇总（writer 拼表用）。"""
    fin = series[year]
    prev = prev_of(series, year)
    row = {
        "收入": _g(fin, "income", "revenue"),
        "归母净利润": _g(fin, "income", "net_profit_parent"),
        "归母净资产": _g(fin, "balance", "equity_parent"),
        "经营现金流": _g(fin, "cashflow", "ocf"),
        "资本开支": _g(fin, "cashflow", "capex"),
        "折旧摊销": da_total(fin),
        "分红": _g(fin, "equity", "dividend"),
        "所有者收益": owner_earnings(fin, prev),
        "ROE": roe(fin, prev),
        "三年增量回报": incremental_return(series, year),
        "净现比": cash_quality(fin),
    }
    return row
