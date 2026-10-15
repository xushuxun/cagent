"""finance_extract 基准：数字层评估

评两件事：① 恒等式通过率（数字层自证）② 与人工核对过的 golden 逐项比对数字。
golden 在 cases/finance_extract.json：每个 role 的 provenance 记了页码与原表行标签，
traps 记了这份年报里真实存在的坑（空白=0、括号负数、母公司口径、重述列等）。

用法：
    uv run benchmark/finance_extract.py            # 评估已有 financials.json 产物
    uv run benchmark/finance_extract.py --selftest # 离线自检：校验器与解析（不需 LLM、不需产物）
    uv run benchmark/finance_extract.py --live     # 先跑抽取再评估（需 LLM 服务）
"""

import argparse
import json
import sys
from pathlib import Path

from cagent.checks import verify
from cagent.fields import duplicate_roles, number, roles_from_rows

CASES_PATH = Path(__file__).parent / "cases" / "finance_extract.json"
DEFAULT_ROOT = Path(__file__).resolve().parents[1] / ".cagent"


def artifact_path(case: dict, root: Path) -> Path:
    return root / case["market"] / case["stock"] / "knowledge" / Path(case["file"]).stem / "financials.json"


def selftest() -> int:
    """离线自检：把踩过的坑固化成断言，改校验器时先过这一关。"""
    failures: list[str] = []

    def expect(name: str, got: object, want: object) -> None:
        if got != want:
            failures.append(f"{name}: 得到 {got!r}，期望 {want!r}")

    expect("括号负数", number("(1,234.50)"), -1234.5)
    expect("千分位", number("225,287,872,883.05"), 225287872883.05)
    expect("破折号即缺数", number("-"), None)
    expect("空白即缺数", number(""), None)
    expect("全角破折号即缺数", number("—"), None)
    expect("已是数值", number(0), 0.0)

    rows = [
        {"label": "净利润", "role": "net_profit", "values": [100.0, 90.0]},
        {"label": "归母净利润", "role": "net_profit_parent", "values": [100.0, 90.0]},
        {"label": "少数股东损益", "role": "minority_profit", "values": [None, 5.0]},
        {"label": "调节项甲", "role": "supplement_other", "values": [30.0, 0.0]},
        {"label": "调节项乙", "role": "supplement_other", "values": [70.0, 0.0]},
        {"label": "无关行", "role": "", "values": [999.0, 999.0]},
    ]
    expect("无 role 与空值行不进 roles", roles_from_rows(rows), {"net_profit": 100.0, "net_profit_parent": 100.0, "supplement_other": 100.0})
    expect("千元单位换算", roles_from_rows(rows, unit="千元")["net_profit"], 100_000.0)
    expect("只取本期列", roles_from_rows(rows, column=1)["supplement_other"], 0.0)

    # 真实踩过的坑：模型给「一、营业总收入」和「其中:营业收入」标了同一个 role，相加会把收入翻倍
    dup_rows = [
        {"label": "一、营业总收入", "role": "revenue", "values": [222.0, 200.0]},
        {"label": "其中:营业收入", "role": "revenue", "values": [222.0, 200.0]},
    ]
    expect("合计行与其中明细行不重复累加", roles_from_rows(dup_rows)["revenue"], 222.0)
    expect("重复标注可查出", duplicate_roles(dup_rows), {"revenue": ["一、营业总收入", "其中:营业收入"]})

    balanced = {"total_assets": 100.0, "total_liabilities": 60.0, "total_equity": 40.0}
    expect("恒等式成立", [c.status for c in verify(balanced) if c.id == "balance.equation"], ["pass"])
    expect("差 1 元判不通过", [c.status for c in verify({**balanced, "total_equity": 41.0}) if c.id == "balance.equation"], ["fail"])
    expect("半分钱在容差内", [c.status for c in verify({**balanced, "total_equity": 40.5}) if c.id == "balance.equation"], ["pass"])
    expect("缺 role 记跳过", [c.status for c in verify({k: v for k, v in balanced.items() if k != "total_equity"}) if c.id == "balance.equation"], ["skip"])
    soft = verify({"cash_end": 10.0, "monetary_funds": 7.0})
    expect("软恒等式记 diff", [c.status for c in soft if c.id == "cross.cash_equivalents"], ["diff"])

    # 真实踩过的坑：补充资料没有「投资性房地产折旧」行时，optional 的 role 缺失既要按 0 计、也不能把校验打崩；
    # 真的漏标了某行，等式照样不平。
    sup = {
        "net_profit_supplement": 100.0,
        "da_fixed": 40.0,
        "da_rou": 10.0,
        "da_intangible": 5.0,
        "da_lt_deferred": 5.0,
        "supplement_other": -60.0,
        "ocf_supplement": 100.0,
    }
    expect("optional role 缺失按 0 计", [c.status for c in verify(sup) if c.id == "supplement.reconcile"], ["pass"])
    expect("漏标一行照样不平", [c.status for c in verify({**sup, "supplement_other": -50.0}) if c.id == "supplement.reconcile"], ["fail"])

    for case in json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]:
        hard_fail = [c.id for c in verify(case["roles"]) if c.status == "fail"]
        expect(f"golden 自洽 {case['id']}", hard_fail, [])

    print("离线自检：" + ("全部通过" if not failures else f"{len(failures)} 项失败"))
    for line in failures:
        print(f"  ✗ {line}")
    return 1 if failures else 0


def evaluate(case: dict, root: Path, tol: float) -> int:
    path = artifact_path(case, root)
    print(f"\n案例 {case['id']}（{case['year']}）")
    print(f"  产物 {path}")
    if not path.exists():
        print("  ✗ 无产物，先跑 cagent/facts.py 或加 --live")
        return 1

    artifact = json.loads(path.read_text(encoding="utf-8"))
    roles = artifact.get("roles") or {}
    checks = verify(roles)  # 按产物数字重算，不信任产物里存的结论
    counts = {status: sum(1 for c in checks if c.status == status) for status in ("pass", "fail", "skip", "diff")}
    print(f"  恒等式 pass {counts['pass']} / fail {counts['fail']} / skip {counts['skip']} / diff {counts['diff']}")

    golden = case["roles"]
    worse, missing, wrong = [], [], []
    for role, want in golden.items():
        got = roles.get(role)
        if got is None:
            missing.append(role)
        elif abs(float(got) - float(want)) > tol:
            wrong.append(f"{role}: 产物 {float(got):,.2f} vs golden {float(want):,.2f}（{case['provenance_by_role'].get(role, '')}）")
    print(f"  数字比对 {len(golden) - len(missing) - len(wrong)}/{len(golden)} 一致（容差 {tol:g} 元）")
    for line in wrong:
        print(f"    ✗ {line}")
    if missing:
        print(f"    ✗ 缺 {len(missing)} 项：{', '.join(missing)}")
    for check in checks:
        if check.status == "fail":
            worse.append(f"{check.id}: Δ={check.delta:,.2f}（{check.desc}）")
    for line in worse:
        print(f"    ✗ {line}")
    return 1 if (wrong or missing or worse) else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="finance_extract 基准")
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    parser.add_argument("--tol", type=float, default=0.5, help="数字比对的绝对容差（元）")
    parser.add_argument("--selftest", action="store_true", help="离线自检校验器与解析，不需要 LLM 与产物")
    parser.add_argument("--live", action="store_true", help="先跑抽取再评估（需 LLM 服务）")
    args = parser.parse_args()

    if args.selftest:
        sys.exit(selftest())

    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]
    root = Path(args.root)

    if args.live:
        from cagent.facts import extract_financials
        from cagent.llm import LLM
        from cagent.scraper.lakehouse_index import atomic_write

        agent = LLM(trace=True)
        for case in cases:
            md = root / case["market"] / case["stock"] / "derived" / case["file"]
            financials = extract_financials(agent, md, root, case["market"], case["stock"])
            out = artifact_path(case, root)
            out.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(out, (json.dumps(financials, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))

    failed = sum(evaluate(case, root, args.tol) for case in cases)
    print(f"\n汇总：{len(cases) - failed}/{len(cases)} 个案例通过")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
