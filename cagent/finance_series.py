"""finance_series 模块：多年序列与机械算术（纯函数，无 LLM、无 IO）

把 finance_extract 产的多年 financials.json 拼成一条序列，再做不含判断的算术：收入去向、现金流结构、
周转与费用率的逐年变化、累计与滚动、所有者收益。判读（哪条重要、算不算异常）留给分析层：
本模块不下结论、不设阈值、不挑指标。

口径：同一年份以最新一份年报为准——最新年报的上期列（已重述）优先于旧年报的本期列，
准则变化与重述就不会在序列里留下断层（course.md 二：报表是人为切片，影子不总与身体等长）。

用法：
    uv run cagent/finance_series.py --stock 601633 --market cn
    uv run cagent/finance_series.py --stock 601633 --market cn --json
"""

import argparse
import json
import logging
import sys
from pathlib import Path

from cagent.finance_identity import DA_ROLES, IDENTITIES, ROLES, UNIT_SCALE

_UNVERIFIED_ROLES = {identity.id: tuple(role for role, _ in identity.terms) for identity in IDENTITIES}

logger = logging.getLogger(__name__)

_YI = 1e8
_MISSING = "—"

_KIND_LABEL = {"balance": "合并资产负债表", "income": "合并利润表", "cashflow": "合并现金流量表", "supplement": "现金流量表补充资料", "equity": "合并股东权益变动表"}


def load_series(root: Path, market: str, stock: str) -> dict[int, dict]:
    """多年 financials.json → {年: {roles, source, basis}}，同一年份以最新年报为准。

    每份年报同时给出本期与上期两套口径：按年升序遍历、逐年覆盖，旧年报的本期数就会被
    新年报的上期数（已重述）顶掉——留下的都是最新表述。
    """
    reports = []
    for path in sorted((Path(root) / market / stock / "knowledge").glob("*/financials.json")):
        reports.append(json.loads(path.read_text(encoding="utf-8")))
    reports.sort(key=lambda fin: int(fin["year"]))
    if not reports:
        raise SystemExit(f"{Path(root) / market / stock / 'knowledge'} 没有 financials.json，先跑 finance_extract.py")

    series: dict[int, dict] = {}
    for fin in reports:
        year, source = int(fin["year"]), fin["source"]["file"]
        # 未通过恒等式的数字下游不得当已核实：把相关 role 按年丢掉
        dropped = set()
        for check in fin.get("identities") or []:
            if check.get("status") == "fail":
                # verbatim 这类校验把具体科目写在 missing 里，恒等式则按 terms 推断
                names = check.get("missing") or _UNVERIFIED_ROLES.get(check.get("id") or "", ())
                dropped |= {name.split("=")[0] for name in names}
        # 完整性从产物内容直接判：序列层要的是三张主表，缺一张这条序列就不可用（不依赖产物里存没存标记）
        tables = set((fin.get("tables") or {}).keys())
        complete = {"balance", "income", "cashflow"} <= tables
        missing = [kind for kind in ("balance", "income", "cashflow", "supplement", "equity") if kind not in tables]
        pages = {kind: value for kind, value in (fin["source"].get("pages") or {}).items() if value}
        supplement = [
            (row.get("label") or "", [value for value in (row.get("values") or [])])
            for row in ((fin.get("tables") or {}).get("supplement") or {}).get("rows") or []
            if any(value is not None for value in (row.get("values") or []))
        ]
        if fin.get("roles"):
            series[year] = {"roles": fin["roles"], "source": source, "basis": "本期", "complete": complete, "missing": missing, "pages": pages, "unit": fin.get("unit") or "元", "supplement": supplement, "dropped": dropped}
        if fin.get("roles_prior"):
            series[year - 1] = {"roles": fin["roles_prior"], "source": source, "basis": "上期（已重述口径）", "complete": complete, "missing": missing, "pages": pages, "unit": fin.get("unit") or "元", "supplement": supplement, "dropped": dropped}
    return series


def _v(roles: dict, name: str, dropped: set[str] | None = None) -> float | None:
    """取 role 的值；该 role 未通过对账（dropped）时按缺数处理。"""
    if dropped and name in dropped:
        return None
    value = roles.get(name)
    return float(value) if value is not None else None


def _div(a: float | None, b: float | None) -> float | None:
    """安全除法；分母为 None/0 返回 None。"""
    if a is None or not b:
        return None
    return a / b


def _sum(values) -> float | None:
    """全部有值才求和；任一缺项返回 None（不拿 0 冒充缺数）。"""
    total = 0.0
    empty = True
    for value in values:
        empty = False
        if value is None:
            return None
        total += value
    return None if empty else total


def _sum_present(values) -> float | None:
    """有值即加、缺项按 0（折旧五行、营运资本分项这类允许缺行的口径）。"""
    total = 0.0
    seen = False
    for value in values:
        if value is None:
            continue
        total += value
        seen = True
    return total if seen else None


def _da_total(roles: dict) -> float | None:
    """折旧摊销合计（补充资料五行）；一行都没有返回 None，缺行按 0 计。"""
    return _sum_present(_v(roles, role) for role in DA_ROLES)


def working_capital(roles: dict) -> tuple[float | None, list[str]]:
    """经营性营运资本 = （应收票据+应收账款+应收款项融资+预付款项+存货+合同资产）−（应付票据+应付账款+合同负债）。

    返回 (金额, 缺项 role)。缺项按 0 计但要报出来——口径不全时所有者收益要打折看。
    """
    assets = ("notes_receivable", "accounts_receivable", "receivables_financing", "prepayments", "inventory", "contract_assets")
    liabs = ("notes_payable", "accounts_payable", "contract_liabilities")
    missing = [role for role in assets + liabs if _v(roles, role) is None]
    if all(_v(roles, role) is None for role in assets) or all(_v(roles, role) is None for role in liabs):
        return None, missing
    value = (_sum_present(_v(roles, role) for role in assets) or 0.0) - (_sum_present(_v(roles, role) for role in liabs) or 0.0)
    return value, missing


def owner_earnings(roles: dict, prior: dict | None) -> float | None:
    """所有者收益（prd.md：巴菲特 1986 口径）= 归母净利润 + 折旧摊销 − 购建长期资产支付的现金 − 经营性营运资本增量。"""
    net_profit, da, capex = _v(roles, "net_profit_parent"), _da_total(roles), _v(roles, "capex")
    if net_profit is None or da is None or capex is None:
        return None
    wc, _ = working_capital(roles)
    wc_prior, _ = working_capital(prior or {})
    if wc is None or wc_prior is None:
        return None   # 没有上年营运资本就减不出增量，宁可留空也不拿 0 冒充
    return net_profit + da - capex - (wc - wc_prior)


def income_split(roles: dict) -> dict[str, float]:
    """收入去向：每 100 元收入被各方分走多少（course.md 八）。股东排最后。

    其他损益与营业外收支是收入以外的进项（政府补贴、投资收益、处置损益），在收入去向里取负号作冲减项：
    收入 = 营业总成本 − 其他损益 − 营业外收支 + 所得税 + 少数股东损益 + 归母净利润，各项相加正好 100。
    """
    revenue = _v(roles, "revenue")
    if not revenue:
        return {}
    operating_profit, total_profit = _v(roles, "operating_profit"), _v(roles, "total_profit")
    cogs_total = _v(roles, "cogs_total")
    parts: dict[str, float | None] = {
        "营业成本": _v(roles, "cogs"),
        "税金及附加": _v(roles, "taxes_surcharges"),
        "销售费用": _v(roles, "selling_exp"),
        "管理费用": _v(roles, "admin_exp"),
        "研发费用": _v(roles, "rd_exp"),
        "财务费用": _v(roles, "fin_exp"),
        "其他损益": None,
        "营业外收支": None,
        "所得税": _v(roles, "income_tax"),
        "少数股东损益": _v(roles, "minority_profit"),
        "归母净利润": _v(roles, "net_profit_parent"),
    }
    if operating_profit is not None and cogs_total is not None:
        listed = _sum([parts[key] for key in ("营业成本", "税金及附加", "销售费用", "管理费用", "研发费用", "财务费用")])
        if listed is not None:
            parts["其他损益"] = (revenue - listed) - operating_profit
    if operating_profit is not None and total_profit is not None:
        parts["营业外收支"] = operating_profit - total_profit
    return {key: value / revenue * 100 for key, value in parts.items() if value is not None}


def cash_check(roles: dict) -> dict[str, float | None]:
    """现金流结构（course.md 九）：三项净额占收入比、净现比、收现比。

    净现比的分母用合并净利润而非归母：经营现金流是整个合并主体的口径，少数股东占比大时用归母会系统性高估。
    """
    revenue = _v(roles, "revenue")
    ocf, icf, fcf = _v(roles, "ocf"), _v(roles, "icf"), _v(roles, "fcf")
    operating_investing = ocf + icf if ocf is not None and icf is not None else None
    return {
        "经营净额/收入": _div(ocf, revenue),
        "投资净额/收入": _div(icf, revenue),
        "筹资净额/收入": _div(fcf, revenue),
        "经营+投资/收入": _div(operating_investing, revenue),
        "三项合计/收入": _div(operating_investing + fcf if operating_investing is not None and fcf is not None else None, revenue),
        "净现比": _div(ocf, _v(roles, "net_profit")),
        "收现比": _div(_v(roles, "cash_from_sales"), revenue),
    }


def expense_ratios(roles: dict) -> dict[str, float | None]:
    """成本费用率：纵向突变的原料（突变由分析层判读，本模块只给数）。"""
    revenue = _v(roles, "revenue")
    return {
        "成本率": _div(_v(roles, "cogs"), revenue),
        "销售费用率": _div(_v(roles, "selling_exp"), revenue),
        "管理费用率": _div(_v(roles, "admin_exp"), revenue),
        "研发费用率": _div(_v(roles, "rd_exp"), revenue),
        "财务费用率": _div(_v(roles, "fin_exp"), revenue),
    }


def turnover(roles: dict, prior: dict | None) -> dict[str, float | None]:
    """周转率（平均余额口径）：纵向突变的原料。"""
    prior = prior or {}

    def average(name: str) -> float | None:
        current, previous = _v(roles, name), _v(prior, name)
        if current is None:
            return None
        return current if previous is None else (current + previous) / 2

    return {
        "应收账款周转率": _div(_v(roles, "revenue"), average("accounts_receivable")),
        "存货周转率": _div(_v(roles, "cogs"), average("inventory")),
        "总资产周转率": _div(_v(roles, "revenue"), average("total_assets")),
    }


def roe(roles: dict, prior: dict | None) -> float | None:
    """ROE = 归母净利润 ÷ 平均归母净资产；序列首年没有上年数时退回期末数。"""
    equity, equity_prior = _v(roles, "equity_parent"), _v((prior or {}), "equity_parent")
    return _div(_v(roles, "net_profit_parent"), (equity + equity_prior) / 2 if equity is not None and equity_prior is not None else equity)


def window_sum(series: dict[int, dict], year: int, window: int, name: str) -> float | None:
    """窗口内某科目的累计（对抗人为切片：course.md 二）。"""
    years = [y for y in range(year - window + 1, year + 1) if y in series]
    if len(years) < window:
        return None
    return _sum(_v(series[y]["roles"], name) for y in years)


def one_yuan_test(series: dict[int, dict], year: int, window: int = 3) -> float | None:
    """一元检验（prd.md）：窗口内每留存 1 元利润变成了多少归母净资产。

    Δ归母净资产 ÷ Σ(归母净利润 − 分红)。Δ 含增发、回购与其他综合收益，比值异常时先查资本配置事件。
    """
    base = series.get(year - window)
    if base is None or year not in series:
        return None
    retained = 0.0
    for offset in range(window):
        item = series.get(year - offset)
        if item is None:
            return None
        net_profit, dividend = _v(item["roles"], "net_profit_parent"), _v(item["roles"], "dividend")
        if net_profit is None or dividend is None:
            return None
        retained += net_profit - dividend
    delta_equity = _v(series[year]["roles"], "equity_parent")
    base_equity = _v(base["roles"], "equity_parent")
    if delta_equity is None or base_equity is None or retained == 0:
        return None
    return (delta_equity - base_equity) / retained


def incremental_return(series: dict[int, dict], year: int, window: int = 3) -> float | None:
    """增量资本回报：窗口内 Δ归母净利润 ÷ Δ归母净资产（prd.md：再投一元赚多少）。"""
    base = series.get(year - window)
    if base is None or year not in series:
        return None
    d_profit = _v(series[year]["roles"], "net_profit_parent")
    b_profit = _v(base["roles"], "net_profit_parent")
    d_equity = _v(series[year]["roles"], "equity_parent")
    b_equity = _v(base["roles"], "equity_parent")
    if d_profit is None or b_profit is None or d_equity is None or b_equity is None or d_equity == b_equity:
        return None
    return (d_profit - b_profit) / (d_equity - b_equity)


def _fmt(value: float | None, digits: int = 1, unit: str = "") -> str:
    if value is None:
        return _MISSING
    return f"{value / _YI:,.{digits}f}{unit}" if unit == "亿" else f"{value:,.{digits}f}{unit}"


def _pct(value: float | None, digits: int = 1) -> str:
    return _MISSING if value is None else f"{value * 100:.{digits}f}%"


def _row(label: str, cells: list[str]) -> str:
    return f"| {label} | " + " | ".join(cells) + " |"


def _table(header: list[str], rows: list[list[str]]) -> str:
    return "\n".join(["| " + " | ".join(header) + " |", "|" + " --- |" * len(header), *(_row(row[0], row[1:]) for row in rows)])


def series_table(series: dict[int, dict], years: list[int]) -> str:
    """多年序列表（亿元）：一门生意为资本工作的结果。"""
    rows = []
    for year in years:
        roles, prior = series[year]["roles"], (series.get(year - 1) or {}).get("roles")
        rows.append(
            [
                str(year),
                _fmt(_v(roles, "revenue"), unit="亿"),
                _fmt(_v(roles, "net_profit_parent"), unit="亿"),
                _fmt(_v(roles, "equity_parent"), unit="亿"),
                _fmt(_v(roles, "ocf"), unit="亿"),
                _fmt(_v(roles, "capex"), unit="亿"),
                _fmt(_da_total(roles), unit="亿"),
                _fmt(_v(roles, "dividend"), unit="亿"),
                _fmt(_retained(roles), unit="亿"),
                _fmt(owner_earnings(roles, prior), unit="亿"),
                _pct(roe(roles, prior)),
                _fmt(_div(_v(roles, "ocf"), _v(roles, "net_profit")), 2),
            ]
        )
    header = ["年份", "收入", "归母净利", "归母净资产", "经营现金流", "资本开支", "折旧摊销", "分红", "留存（归母净利−分红）", "所有者收益", "ROE", "净现比"]
    return _table(header, rows)


def income_level_table(series: dict[int, dict], years: list[int]) -> str:
    """利润表主要科目（亿元）：给绝对金额，模型就不必用比率反推。"""
    order = [
        ("revenue", "营业总收入"),
        ("cogs", "营业成本"),
        ("taxes_surcharges", "税金及附加"),
        ("selling_exp", "销售费用"),
        ("admin_exp", "管理费用"),
        ("rd_exp", "研发费用"),
        ("fin_exp", "财务费用"),
        ("operating_profit", "营业利润"),
        ("total_profit", "利润总额"),
        ("income_tax", "所得税费用"),
        ("net_profit", "净利润"),
        ("net_profit_parent", "归母净利润"),
    ]
    rows = [[str(year), *[_fmt(_v(series[year]["roles"], role), unit="亿") for role, _ in order]] for year in years]
    return _table(["年份", *[label for _, label in order]], rows)


def income_use_table(series: dict[int, dict], years: list[int]) -> str:
    order = ["营业成本", "税金及附加", "销售费用", "管理费用", "研发费用", "财务费用", "其他损益", "营业外收支", "所得税", "少数股东损益", "归母净利润"]
    rows = []
    for year in years:
        split = income_split(series[year]["roles"])
        if not split:
            continue
        rows.append([str(year), *[_fmt(split.get(key), 1) for key in order]])
    return _table(["年份", *order], rows)


def cash_table(series: dict[int, dict], years: list[int]) -> str:
    order = ["经营净额/收入", "投资净额/收入", "筹资净额/收入", "经营+投资/收入", "三项合计/收入", "净现比", "收现比"]
    rows = []
    for year in years:
        check = cash_check(series[year]["roles"])
        rows.append([str(year), *[_pct(check[key]) if key.endswith("收入") else _fmt(check[key], 2) for key in order]])
    return _table(["年份", *order], rows)


def ratio_table(series: dict[int, dict], years: list[int]) -> str:
    pct_keys = ("成本率", "销售费用率", "管理费用率", "研发费用率", "财务费用率")
    order = [*pct_keys, "应收账款周转率", "存货周转率", "总资产周转率"]
    rows = []
    for year in years:
        ratios = {**expense_ratios(series[year]["roles"]), **turnover(series[year]["roles"], (series.get(year - 1) or {}).get("roles"))}
        rows.append([str(year), *[_pct(ratios[key]) if key in pct_keys else _fmt(ratios[key], 2) for key in order]])
    return _table(["年份", *order], rows)


def _yoy(roles: dict, prior: dict, name: str) -> float | None:
    """同比增速；上年数缺失或为零返回 None。"""
    current, previous = _v(roles, name), _v(prior, name)
    if current is None or previous in (None, 0):
        return None
    return (current - previous) / abs(previous)


def cash_reconciliation_table(series: dict[int, dict], years: list[int]) -> str:
    """经营活动现金流量调节表（亿元）：净利润到经营现金流的逐项桥。

    利润与现金为什么对不上，看这张表；数据来自现金流量表补充资料。
    """
    year = years[-1]
    rows = series[year].get("supplement") or []
    if not rows:
        return ""
    scale = UNIT_SCALE.get((series[year].get("unit") or "元").strip(), 1.0)
    body = []
    for label, values in rows:
        cells = [_fmt(values[i] * scale if i < len(values) and values[i] is not None else None, unit="亿", digits=2) for i in range(2)]
        body.append([label, *cells])
    header = ["项目", f"{year} 年", f"{year - 1} 年"]
    return _table(header, body)


def growth_table(series: dict[int, dict], years: list[int]) -> str:
    """同比变化与自由现金流：这些数模型不该自己算，由程序给。"""
    rows = []
    for year in years:
        roles, prior = series[year]["roles"], (series.get(year - 1) or {}).get("roles") or {}
        ocf, capex = _v(roles, "ocf"), _v(roles, "capex")
        free_cash_flow = ocf - capex if ocf is not None and capex is not None else None
        rows.append(
            [
                str(year),
                _pct(_yoy(roles, prior, "revenue")),
                _pct(_yoy(roles, prior, "net_profit_parent")),
                _pct(_yoy(roles, prior, "equity_parent")),
                _pct(_yoy(roles, prior, "ocf")),
                _fmt(free_cash_flow, unit="亿"),
                _pct(_div(_v(roles, "dividend"), _v(roles, "net_profit_parent"))),
            ]
        )
    header = ["年份", "收入同比", "归母净利同比", "归母净资产同比", "经营现金流同比", "自由现金流（经营现金流−资本开支，亿元）", "分红/归母净利"]
    return _table(header, rows)


def asset_quality_table(series: dict[int, dict], years: list[int]) -> str:
    """资产质量（亿元）：应收能不能收回、存货要不要减值、钱有没有被限住。

    全部取自年报附注；「两年以上应收/资产总计」是课程引的学术信号（Zhu & Jiang, CJAR 2009）。
    公司特有的明细（商誉、开发支出、担保承诺等）不进固定表，重构后由按需明细清单承载。
    """
    rows = []
    for year in years:
        roles, dropped = series[year]["roles"], series[year].get("dropped") or set()
        ar_gross, ar_provision = _v(roles, "ar_gross", dropped), _v(roles, "ar_provision", dropped)
        rows.append(
            [
                str(year),
                _fmt(ar_gross, unit="亿"),
                _fmt(ar_provision, unit="亿"),
                _pct(_div(ar_provision, ar_gross)),
                _fmt(_v(roles, "ar_aging_over_2y", dropped), unit="亿"),
                _pct(_div(_v(roles, "ar_aging_over_2y", dropped), _v(roles, "total_assets", dropped))),
                _fmt(_v(roles, "inventory_gross", dropped), unit="亿"),
                _fmt(_v(roles, "inventory_provision", dropped), unit="亿"),
                _fmt(_v(roles, "restricted_cash", dropped), unit="亿"),
                _pct(_div(_v(roles, "restricted_cash", dropped), _v(roles, "monetary_funds", dropped))),
            ]
        )
    header = ["年份", "应收账款账面余额", "坏账准备", "坏账准备/应收", "账龄两年以上应收", "两年以上/资产总计", "存货账面余额", "存货跌价准备", "受限货币资金", "受限/货币资金"]
    return _table(header, rows)


def ratio_delta_table(series: dict[int, dict], years: list[int]) -> str:
    """费用率与周转率的同比变化：纵向找突变的原料。率用百分点 pp，周转率用次。"""
    pct_keys = ("成本率", "销售费用率", "管理费用率", "研发费用率", "财务费用率")
    turnover_keys = ("应收账款周转率", "存货周转率", "总资产周转率")
    rows = []
    for year in years:
        ratios = {**expense_ratios(series[year]["roles"]), **turnover(series[year]["roles"], (series.get(year - 1) or {}).get("roles"))}
        prior = series.get(year - 1)
        prior_ratios = {**expense_ratios(prior["roles"]), **turnover(prior["roles"], (series.get(year - 2) or {}).get("roles"))} if prior else {}
        cells = []
        for key in (*pct_keys, *turnover_keys):
            current, previous = ratios.get(key), prior_ratios.get(key)
            if current is None or previous is None:
                cells.append(_MISSING)
            elif key in pct_keys:
                cells.append(f"{(current - previous) * 100:+.1f}pp")
            else:
                cells.append(f"{current - previous:+.2f}")
        rows.append([str(year), *cells])
    header = ["年份", *[f"{key}Δ" for key in pct_keys], *[f"{key}Δ" for key in turnover_keys]]
    return _table(header, rows)


def capital_structure_table(series: dict[int, dict], years: list[int]) -> str:
    """资本与负债结构（亿元）：回报是不是靠杠杆撑起来的（prd.md 反向清单第一条）。"""
    rows = []
    for year in years:
        roles = series[year]["roles"]
        equity = _v(roles, "equity_parent")
        debt = _sum_present(_v(roles, name) for name in ("short_term_borrowings", "long_term_borrowings", "bonds_payable", "lease_liabilities"))
        cash = _v(roles, "monetary_funds")
        rows.append(
            [
                str(year),
                _fmt(equity, unit="亿"),
                _fmt(debt, unit="亿"),
                _fmt(cash, unit="亿"),
                _fmt(cash - debt if cash is not None and debt is not None else None, unit="亿"),
                _pct(_div(debt, equity)),
                _pct(_div(cash, debt)),
            ]
        )
    header = ["年份", "归母净资产", "有息负债", "货币资金", "净现金（货币资金−有息负债）", "有息负债/归母净资产", "货币资金/有息负债"]
    return _table(header, rows)


def _retained(roles: dict) -> float | None:
    """当年留存 = 归母净利润 − 分红。"""
    profit, dividend = _v(roles, "net_profit_parent"), _v(roles, "dividend")
    return profit - dividend if profit is not None and dividend is not None else None


def retained_sum(series: dict[int, dict], year: int, window: int) -> float | None:
    """窗口内累计留存（归母净利润 − 分红）：留下的钱是资本回报复利的来源（prd.md）。"""
    profit = window_sum(series, year, window, "net_profit_parent")
    dividend = window_sum(series, year, window, "dividend")
    return profit - dividend if profit is not None and dividend is not None else None


def cumulative_table(series: dict[int, dict], years: list[int], window: int = 3) -> str:
    rows = []
    for year in years:
        rows.append(
            [
                str(year),
                _fmt(window_sum(series, year, window, "net_profit_parent"), unit="亿"),
                _fmt(window_sum(series, year, window, "dividend"), unit="亿"),
                _fmt(retained_sum(series, year, window), unit="亿"),
                _fmt(window_sum(series, year, window, "ocf"), unit="亿"),
                _fmt(window_sum(series, year, window, "capex"), unit="亿"),
                _fmt(window_sum(series, year, window, "capital_raised"), unit="亿"),
                _pct(incremental_return(series, year, window)),
                _fmt(one_yuan_test(series, year, window), 2),
            ]
        )
    header = [
        "年份",
        f"近{window}年累计归母净利",
        f"近{window}年累计分红",
        f"近{window}年累计留存",
        f"近{window}年累计经营现金流",
        f"近{window}年累计资本开支",
        f"近{window}年累计股东投入",
        f"{window}年增量资本回报",
        "一元检验",
    ]
    return _table(header, rows)


def evidence(series: dict[int, dict], years: list[int] | None = None) -> str:
    """给分析层看的证据表：全是程序算出的数，不含判断、不含结论。"""
    years = years or sorted(series)
    sections = [
        ("多年序列（亿元）", series_table),
        ("利润表科目（亿元）", income_level_table),
        ("同比变化与自由现金流", growth_table),
        ("经营活动现金流量调节表（亿元）", cash_reconciliation_table),
        ("每 100 元收入去向（元）", income_use_table),
        ("现金流结构（占收入比；净现比、收现比为倍数）", cash_table),
        ("资产质量（亿元；取自年报附注）", asset_quality_table),
        ("资本与负债结构（亿元）", capital_structure_table),
        ("成本费用率与周转率（纵向突变的原料）", ratio_table),
        ("费用率与周转率的同比变化（率用百分点 pp，周转率用次）", ratio_delta_table),
        ("累计与滚动（亿元）", cumulative_table),
    ]
    parts = [f"# {years[-1]} 年财务证据表（程序直解与算术，未作判断）\n"]
    for title, builder in sections:
        table = builder(series, years)
        if table.count("\n") > 1:   # 只有表头没有数据行的表不打出来
            parts += [f"## {title}\n", table]
    parts.append("\n## 口径与来源\n")
    parts.append("\n".join(f"- {year}：{series[year]['source']}（{series[year]['basis']}）" for year in years))
    for year in years:
        pages = series[year].get("pages") or {}
        if pages:
            detail = "；".join(f"{_KIND_LABEL.get(kind, kind)} p.{'-'.join(str(p) for p in value)}" for kind, value in pages.items())
            parts.append(f"- {year} 表页：{detail}")
    dropped = sorted({role for year in years for role in (series[year].get("dropped") or set())})
    if dropped:
        parts.append(f"- 下列科目未通过附注对账，已按缺数处理：{'、'.join(ROLES.get(role, role) for role in dropped)}")
    incomplete = [f"{year}（缺 {'、'.join(series[year].get('missing') or [])}）" for year in years if series[year].get("complete") is False]
    if incomplete:
        parts.append(f"- 产物不完整的年份，相关指标留空：{'；'.join(incomplete)}")
    missing = sorted({role for year in years for role in _missing_roles(series[year]["roles"])})
    if missing:
        parts.append(f"- 缺项（按 0 计入营运资本，所有者收益口径因此偏宽）：{'、'.join(ROLES.get(role, role) for role in missing)}")
    parts.append("- 所有者收益 = 归母净利润 + 折旧摊销 − 购建长期资产支付的现金 − 经营性营运资本增量（prd.md 口径）")
    parts.append("- 收入去向：其他损益、营业外收支是收入以外的进项，作冲减项取负号；各项相加等于 100 元")
    parts.append("- 同比、自由现金流、分红比例均由程序计算；引用时照抄本表，不要自行换算或再计算")
    parts.append("- 有息负债 = 短期借款 + 长期借款 + 应付债券 + 租赁负债；股东投入取权益变动表「所有者投入和减少资本」")
    parts.append("- 留存 = 当年归母净利润 − 当年分红；所有者收益是另一个口径（现金口径），两者不要混用")
    parts.append("- 资产质量取自年报附注：账龄两年以上应收按原表年限口径（只按信用期披露的年份留空）；受限货币资金照原文披露")
    parts.append(
        "- 经营活动现金流量调节表按「调整后」口径列示：正数表示该项目使经营现金流增加。"
        "「经营性应收项目的增加(减少)」为正表示应收项目净减少（回款大于新增），为负表示净增加（占用现金）；"
        "「存货的减少(增加)」同理"
    )
    parts.append("- 逐年数字取自最新一份年报的口径（上期列即已重述数），来源见上")
    return "\n".join(parts) + "\n"


def _missing_roles(roles: dict) -> list[str]:
    _, missing = working_capital(roles)
    return missing


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S", stream=sys.stderr)
    parser = argparse.ArgumentParser(description="finance_series：多年序列与机械算术")
    parser.add_argument("--stock", default="601633")
    parser.add_argument("--market", default="cn", choices=["cn", "hk"])
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1] / ".cagent"))
    parser.add_argument("--last", type=int, default=5, help="证据表取最近几年")
    parser.add_argument("--json", action="store_true", help="同时落盘 series.json（供横向对比复用）")
    args = parser.parse_args()

    series = load_series(Path(args.root), args.market, args.stock)
    years = sorted(series)[-args.last :]
    logger.info(f"年份 {years}（共 {len(series)} 年）")
    print(evidence(series, years))

    if args.json:
        out = Path(args.root) / args.market / args.stock / "series.json"
        payload = {"market": args.market, "stock": args.stock, "years": years, "series": {str(year): series[year] for year in years}}
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        logger.info(f"输出 {out}")


if __name__ == "__main__":
    main()
