"""报告层基准：文档不变量检查

可自动化的部分（不需要 LLM 裁判）：
    结构      四个部分齐全、14 问各有一节、每节都有正文
    数字越界  生意模式与资本回报里出现的每个数字都必须能在「四、数据」里找到——
              模型不得自造、换算、外推或累加数字

用法：
    uv run benchmark/finance_analysis.py            # 检查已有 report.md
    uv run benchmark/finance_analysis.py --selftest # 离线自检解析与判定，不需要 LLM 与产物
"""

import argparse
import json
import re
import sys
from pathlib import Path

from cagent.checks import allowed_numbers, numbers_in, on_evidence

CASES_PATH = Path(__file__).parent / "cases" / "finance_analysis.json"
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "output" / "wiki"
DEFAULT_ROOT = Path(__file__).resolve().parents[1] / ".cagent"

_PAGE_MARK = re.compile(r"<!-- page (\d+) -->")
_PAGE_CITE = re.compile(r"p\.\s?(\d+)")
_YEAR = re.compile(r"(?:19|20)\d{2}")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_SECTIONS = ("## 生意模式", "## 历年经营决策", "## 资本回报", "## 数据")
_QUESTION_HEADINGS = 14
_NUMBERED = re.compile(r"(?m)^#+\s+(?:[一二三四五六七八九十]+、|\d+[.、]\s)")
_CITATION = re.compile(r"（见年报|（来源[:：]|course\.md")


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
    expect("同值命中", on_evidence(2228.2, 1, allowed, tol=0.0), True)
    expect("按小数位取整命中", on_evidence(2228, 0, allowed, tol=0.0), True)
    # 交付形态的两条硬约束：正文不留出处、标题不带编号
    expect("正文残留出处会被抓出", bool(_CITATION.search("增长 12%（见年报 p.31）")), True)
    expect("干净正文不报出处", bool(_CITATION.search("增长 12%，主要来自出口")), False)
    expect("编号标题会被抓出", bool(_NUMBERED.search("## 一、生意模式")), True)
    expect("无编号标题不报", bool(_NUMBERED.search("## 生意模式")), False)
    expect("自造数字不命中", on_evidence(2229.5, 1, allowed, tol=0.0), False)
    expect("外推数字不命中", on_evidence(4.4, 1, allowed, tol=0.0), False)

    print("离线自检：" + ("全部通过" if not failures else f"{len(failures)} 项失败"))
    for line in failures:
        print(f"  ✗ {line}")
    return 1 if failures else 0


def evaluate(case: dict, output_root: Path, root: Path) -> int:
    stock, market = case["stock"], case["market"]
    doc_path = output_root / market / stock / "report.md"
    print(f"\n案例 {market}/{stock}")
    print(f"  文档 {doc_path}")
    if not doc_path.exists():
        print("  ✗ 无 report.md，先跑 cagent/report.py")
        return 1

    doc = doc_path.read_text(encoding="utf-8")
    problems: list[str] = []

    for section in _SECTIONS:
        if section not in doc:
            problems.append(f"缺部分 {section}")

    # 数据部分在最后：它之前的正文里出现的每个数字都必须能在数据里找到
    data_index = doc.find(_SECTIONS[3])
    narrative, answers = (doc[: doc.find(_SECTIONS[2])], doc[doc.find(_SECTIONS[2]): data_index]) if data_index >= 0 else (doc, "")
    evidence_part = doc[data_index:] if data_index >= 0 else ""
    evidence_numbers = allowed_numbers(evidence_part)
    # 叙述节的数字还可以来自年报原文（读过的那几章），把原文并进允许集合
    corpus = "\n".join(path.read_text(encoding="utf-8") for path in sorted((root / market / stock / "derived").glob("*.md")))
    narrative_numbers = evidence_numbers | allowed_numbers(corpus)
    violations = []
    for section, allowed in ((narrative, narrative_numbers), (answers, evidence_numbers)):
        for token, decimals in numbers_in(section):
            value = float(token.replace(",", ""))
            if not on_evidence(value, decimals, allowed, tol=case.get("tol", 0.0)):
                violations.append(token)
    limit = case.get("max_out_of_scope", 0)
    print(f"  数据部分数字 {len(evidence_numbers)} 个；正文越界 {len(violations)} 个（允许 {limit}；叙述节另许年报原文）")
    for token in violations[:10]:
        print(f"    ✗ {token}")
    if len(violations) > limit:
        problems.append(f"越界数字 {len(violations)} 个")

    # 正文不该留出处标记（出处收在 sources.md 里），也不该带编号——这两条是交付形态的硬约束
    citations = _CITATION.findall(doc)
    if citations:
        problems.append(f"正文残留出处标记 {len(citations)} 处（应只出现在 sources.md）")
    numbered = _NUMBERED.findall(doc)
    if numbered:
        problems.append(f"标题带编号 {len(numbered)} 处（顺序由侧边目录表达）")

    # 14 问各一节，且每节都有正文（标题由程序写死，少一节说明装配坏了）
    headings = re.findall(r"(?m)^### (.+)$", _section_span(doc))
    expected = case.get("questions", _QUESTION_HEADINGS)
    print(f"  问题小节 {len(headings)} 个，期望 {expected}")
    if expected and len(headings) != expected:
        problems.append(f"问题小节 {len(headings)} 个，期望 {expected}")
    empty = [title for title in headings if not _section_body(doc, title).strip()]
    if empty:
        problems.append(f"这些问题小节没有正文：{'、'.join(empty)}")

    missing_terms = [term for term in case.get("required_terms", []) if term not in doc]
    print(f"  必备内容 {len(case.get('required_terms', [])) - len(missing_terms)}/{len(case.get('required_terms', []))} 出现")
    if missing_terms:
        problems.append(f"报告缺内容：{'、'.join(missing_terms)}")

    for line in problems:
        print(f"  ✗ {line}")
    return 1 if problems else 0


def _section_span(doc: str) -> str:
    """资本回报部分的正文（两个二级标题之间）。"""
    start = doc.find("## 资本回报")
    end = doc.find("## 数据")
    return doc[start:end] if start >= 0 and end > start else ""


def _section_body(doc: str, title: str) -> str:
    """取某个问题小节的正文（到下一个同级标题为止）。"""
    match = re.search(rf"(?m)^### {re.escape(title)}$", doc)
    if not match:
        return ""
    rest = doc[match.end():]
    nxt = re.search(r"(?m)^###? ", rest)
    return rest[: nxt.start()] if nxt else rest


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
