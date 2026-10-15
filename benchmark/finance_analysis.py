"""finance 分析层基准：文档不变量检查

可自动化的部分（不需要 LLM 裁判）：
    结构      两个小节齐全、拆解覆盖必备内容
    数字越界  拆解节里出现的每个数字都必须能在证据表里找到——模型不得自造、换算、外推或累加数字

用法：
    uv run benchmark/finance_analysis.py            # 检查已有 finance.md
    uv run benchmark/finance_analysis.py --selftest # 离线自检解析与判定，不需要 LLM 与产物
"""

import argparse
import json
import re
import sys
from pathlib import Path

CASES_PATH = Path(__file__).parent / "cases" / "finance_analysis.json"
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "output" / "wiki"
DEFAULT_ROOT = Path(__file__).resolve().parents[1] / ".cagent"

_PAGE_MARK = re.compile(r"<!-- page (\d+) -->")
_PAGE_CITE = re.compile(r"p\.\s?(\d+)")
_YEAR = re.compile(r"(?:19|20)\d{2}")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_SECTIONS = ("## 一、数据", "## 二、资本回报拆解")


def numbers_in(text: str) -> list[tuple[str, int]]:
    """文本里的数字 → [(原样, 小数位数)]；先摘掉页码引用与年份，它们不是数据声明。"""
    cleaned = _PAGE_MARK.sub(" ", text)
    cleaned = _PAGE_CITE.sub(" ", cleaned)
    cleaned = _YEAR.sub(" ", cleaned)
    cleaned = re.sub(r"(?m)^\s*\d+[.、)]\s", " ", cleaned)  # 列表序号
    found = []
    for token in _NUMBER.findall(cleaned):
        digits = token.split(".")[1] if "." in token else ""
        found.append((token, len(digits)))
    return found


def matches(value: float, decimals: int, allowed: set[float], tol: float) -> bool:
    """正文数字是否落在证据表某个数上（按正文写到的小数位取整比对）。"""
    for candidate in allowed:
        if abs(candidate - value) <= max(tol, 0.5 * 10 ** (-decimals) + 1e-9):
            return True
    return False


def _allowed_numbers(evidence_text: str) -> set[float]:
    allowed = set()
    for token, _ in numbers_in(evidence_text):
        allowed.add(float(token.replace(",", "")))
    return allowed


def selftest() -> int:
    failures: list[str] = []

    def expect(name: str, got: object, want: object) -> None:
        if got != want:
            failures.append(f"{name}: 得到 {got!r}，期望 {want!r}")

    expect("摘掉页码标记", numbers_in("营业成本 82.0 元 <!-- page 142 -->"), [("82.0", 1)])
    expect("摘掉页码引用", numbers_in("见 p.246 的补充资料"), [])
    expect("摘掉年份", numbers_in("2025 年收入 2,228.2 亿"), [("2,228.2", 1)])
    expect("摘掉列表序号", numbers_in("1. 收入 100 元"), [("100", 0)])
    expect("保留百分比", numbers_in("占比 18.1%"), [("18.1", 1)])

    allowed = {2228.2, 18.1, 403.6}
    expect("同值命中", matches(2228.2, 1, allowed, 0.0), True)
    expect("按小数位取整命中", matches(2228, 0, allowed, 0.0), True)
    expect("自造数字不命中", matches(2229.5, 1, allowed, 0.0), False)
    expect("外推数字不命中", matches(4.4, 1, allowed, 0.0), False)

    print("离线自检：" + ("全部通过" if not failures else f"{len(failures)} 项失败"))
    for line in failures:
        print(f"  ✗ {line}")
    return 1 if failures else 0


def evaluate(case: dict, output_root: Path, root: Path) -> int:
    stock, market = case["stock"], case["market"]
    doc_path = output_root / market / stock / "finance.md"
    print(f"\n案例 {market}/{stock}")
    print(f"  文档 {doc_path}")
    if not doc_path.exists():
        print("  ✗ 无 finance.md，先跑 cagent/wiki_finance.py")
        return 1

    doc = doc_path.read_text(encoding="utf-8")
    problems: list[str] = []

    for section in _SECTIONS:
        if section not in doc:
            problems.append(f"缺小节 {section}")
    level2 = re.findall(r"(?m)^## ", doc)
    if len(level2) != 2:
        problems.append(f"二级标题 {len(level2)} 个，期望 2")

    evidence_part, _, prose = doc.partition(_SECTIONS[1])
    evidence_numbers = _allowed_numbers(evidence_part)

    # 全篇数字只许来自证据表：模型自造、换算、外推、累加都会在这里露馅
    violations = []
    for token, decimals in numbers_in(prose):
        value = float(token.replace(",", ""))
        if not matches(value, decimals, evidence_numbers, case.get("tol", 0.0)):
            violations.append(token)
    limit = case.get("max_out_of_scope", 0)
    print(f"  证据表数字 {len(evidence_numbers)} 个；正文越界 {len(violations)} 个（允许 {limit}）")
    for token in violations[:10]:
        print(f"    ✗ {token}")
    if len(violations) > limit:
        problems.append(f"越界数字 {len(violations)} 个")

    # 只在拆解正文里查必备内容：整篇匹配会被数据表的表头蒙混过去
    missing_terms = [term for term in case.get("required_terms", []) if term not in prose]
    print(f"  必备内容 {len(case.get('required_terms', [])) - len(missing_terms)}/{len(case.get('required_terms', []))} 出现")
    if missing_terms:
        problems.append(f"拆解缺内容：{'、'.join(missing_terms)}")

    # 维度标题由程序写死，少一条说明装配坏了，与模型笔下松紧无关
    headings = re.findall(r"(?m)^### (\d+)\. ", prose)
    expected = case.get("dimensions", 0)
    print(f"  维度标题 {len(headings)} 个，期望 {expected}")
    if expected and len(headings) != expected:
        problems.append(f"维度标题 {len(headings)} 个，期望 {expected}")

    for line in problems:
        print(f"  ✗ {line}")
    return 1 if problems else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="finance 分析层基准")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    parser.add_argument("--selftest", action="store_true", help="离线自检解析与判定")
    args = parser.parse_args()

    if args.selftest:
        sys.exit(selftest())

    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]
    failed = sum(evaluate(case, Path(args.output), Path(args.root)) for case in cases)
    print(f"\n汇总：{len(cases) - failed}/{len(cases)} 个案例通过")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
