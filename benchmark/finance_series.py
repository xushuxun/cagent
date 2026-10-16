"""finance_series 基准：序列层评估

评两件事：① 算术本身（离线自检：收入去向、所有者收益、一元检验、口径覆盖、窗口累计，用构造数据）
② 真实公司的逐年数字（golden 存 cases/finance_series.json，每个数都能溯源到年报页码）。

用法：
    uv run benchmark/finance_series.py            # 对已有抽取产物评 golden
    uv run benchmark/finance_series.py --selftest # 离线自检，不需要 LLM 与产物
"""

import argparse
import json
import sys
from pathlib import Path

from cagent.series import (
    cash_check,
    evidence,
    income_split,
    incremental_return,
    load_series,
    one_yuan_test,
    owner_earnings,
    turnover,
    window_sum,
    working_capital,
)

CASES_PATH = Path(__file__).parent / "cases" / "finance_series.json"
DEFAULT_ROOT = Path(__file__).resolve().parents[1] / ".cagent"


def _income(**overrides: float) -> dict:
    """一份够用的利润表口径：收入 100、成本 60、费用 10、其他损益 5、税 5、归母净利 30。"""
    roles = {
        "revenue": 100.0,
        "cogs_total": 70.0,
        "cogs": 60.0,
        "taxes_surcharges": 2.0,
        "selling_exp": 3.0,
        "admin_exp": 3.0,
        "rd_exp": 2.0,
        "fin_exp": 0.0,
        "operating_profit": 35.0,
        "total_profit": 35.0,
        "income_tax": 5.0,
        "net_profit": 30.0,
        "net_profit_parent": 30.0,
        "minority_profit": 0.0,
    }
    roles.update(overrides)
    return roles


def selftest() -> int:
    failures: list[str] = []

    def expect(name: str, got: object, want: object) -> None:
        if got != want:
            failures.append(f"{name}: 得到 {got!r}，期望 {want!r}")

    def close(name: str, got: float | None, want: float, tol: float = 1e-9) -> None:
        if got is None or abs(got - want) > tol:
            failures.append(f"{name}: 得到 {got!r}，期望 {want!r}")

    split = income_split(_income())
    close("收入去向各项之和 = 100 元", sum(split.values()), 100.0, 1e-6)
    close("收入去向-股东排最后", split["归母净利润"], 30.0)
    # 其他损益是收入以外的进项（营业利润 35 −（收入 100 − 营业总成本 70）= 5），在收入去向里作冲减项、取负号
    close("收入去向-其他损益作冲减项", split["其他损益"], -5.0)

    check = cash_check({"revenue": 100.0, "ocf": 25.0, "icf": -10.0, "fcf": -5.0, "net_profit": 20.0, "cash_from_sales": 110.0})
    close("经营+投资占收入比", check["经营+投资/收入"], 0.15)
    close("三项合计占收入比", check["三项合计/收入"], 0.10)
    close("净现比", check["净现比"], 1.25)
    close("收现比", check["收现比"], 1.10)

    # 所有者收益：净利 30 + 折旧 10 − 资本开支 12 − 营运资本增量 3
    prior = {"notes_receivable": 0.0, "accounts_receivable": 10.0, "receivables_financing": 0.0, "prepayments": 0.0, "inventory": 10.0, "contract_assets": 0.0, "notes_payable": 0.0, "accounts_payable": 5.0, "contract_liabilities": 0.0}
    current = {**prior, "accounts_receivable": 13.0}
    roles = {**_income(), "net_profit_parent": 30.0, "capex": 12.0, "da_fixed": 10.0, **current}
    close("营运资本", working_capital(roles)[0], 18.0)
    close("所有者收益", owner_earnings(roles, prior), 25.0)
    expect("缺上年营运资本时所有者收益留空（不拿 0 冒充）", owner_earnings(roles, None), None)

    # 口径覆盖：新年报的上期列（已重述）应顶掉旧年报的本期列（load_series 的真实行为在 golden 里逐年验）
    covered: dict[int, dict] = {}
    for year, revenue, basis in ((2023, 100.0, "本期"), (2024, 110.0, "本期"), (2023, 105.0, "上期")):
        covered[year] = {"roles": {"revenue": revenue}, "source": "fy.md", "basis": basis}
    expect("新年报上期列顶掉旧年报本期列", covered[2023]["roles"]["revenue"], 105.0)
    expect("上期口径标注来源", covered[2023]["basis"], "上期")

    equity = {2021: 100.0, 2022: 120.0, 2023: 150.0, 2024: 190.0}
    profit = {2021: 10.0, 2022: 15.0, 2023: 20.0, 2024: 25.0}
    dividend = {2021: 0.0, 2022: 5.0, 2023: 5.0, 2024: 5.0}
    window = {y: {"roles": {"equity_parent": equity[y], "net_profit_parent": profit[y], "dividend": dividend[y]}, "source": "t", "basis": "本期"} for y in equity}
    # 窗口 2022–2024 留存 = (15−5)+(20−5)+(25−5) = 45，期初（2021）归母权益 100 → 期末 190
    close("一元检验", one_yuan_test(window, 2024, 3), 90.0 / 45.0)
    close("三年增量资本回报", incremental_return(window, 2024, 3), (25.0 - 10.0) / (190.0 - 100.0))
    close("窗口累计归母净利", window_sum(window, 2024, 3, "net_profit_parent"), 60.0)
    expect("窗口不足返回空", window_sum(window, 2022, 3, "net_profit_parent"), None)

    avg = turnover({"revenue": 120.0, "cogs": 60.0, "accounts_receivable": 20.0}, {"accounts_receivable": 10.0})
    close("应收周转率用平均余额", avg["应收账款周转率"], 8.0)
    expect("收入去向缺收入即留空", income_split({"revenue": None}), {})

    for case in json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]:
        missing = [year for year, roles in case["years"].items() if "revenue" not in roles]
        expect(f"golden 完整 {case['id']}", missing, [])
        # 收入去向必须正好把 100 元收入分完——符号弄错（其他损益／营业外当加项）这条就会露馅
        for year, roles in case["years"].items():
            split = income_split(roles)
            if split:
                close(f"golden 收入去向各项之和 = 100（{case['id']} {year}）", sum(split.values()), 100.0, 1e-6)

    print("离线自检：" + ("全部通过" if not failures else f"{len(failures)} 项失败"))
    for line in failures:
        print(f"  ✗ {line}")
    return 1 if failures else 0


def evaluate(case: dict, root: Path) -> int:
    print(f"\n案例 {case['id']}（{case.get('provenance', '')[:40]}…）")
    series = load_series(root, case["market"], case["stock"])
    bad: list[str] = []
    for year_text, want in sorted(case["years"].items()):
        year = int(year_text)
        if year not in series:
            bad.append(f"{year}: 序列里没有这一年")
            continue
        roles = series[year]["roles"]
        for role, expected in want.items():
            got = roles.get(role)
            if got is None:
                bad.append(f"{year} {role}: 产物缺这一项（golden {expected:,.2f}）")
            elif abs(float(got) - float(expected)) > max(0.5, abs(float(expected)) * 1e-9):
                bad.append(f"{year} {role}: 产物 {float(got):,.2f} vs golden {float(expected):,.2f}")
    years = sorted(series)
    print(f"  年份 {years[0]}–{years[-1]}（{len(years)} 年），口径：{series[years[-1]]['source']}")
    for year in sorted(series):
        item = series[year]
        print(f"    {year}：{item['source']}（{item['basis']}）")
    for line in bad:
        print(f"    ✗ {line}")
    print(f"  逐年比对 {sum(len(v) for v in case['years'].values()) - len(bad)}/{sum(len(v) for v in case['years'].values())} 一致")
    return 1 if bad else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="finance_series 基准")
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    parser.add_argument("--selftest", action="store_true", help="离线自检算术，不需要 LLM 与产物")
    parser.add_argument("--evidence", action="store_true", help="同时打印证据表")
    args = parser.parse_args()

    if args.selftest:
        sys.exit(selftest())

    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]
    root = Path(args.root)
    if args.evidence:
        for case in cases:
            print(evidence(load_series(root, case["market"], case["stock"])))
    failed = sum(evaluate(case, root) for case in cases)
    print(f"\n汇总：{len(cases) - failed}/{len(cases)} 个案例通过")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
