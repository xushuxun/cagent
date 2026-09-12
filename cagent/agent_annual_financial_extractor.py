import io
import json
import math
import re
from contextlib import redirect_stdout
from pathlib import Path

from cagent.agent import Agent
from cagent.doc import AnnualReport


def run_code(code: str, namespace: dict) -> str:
    """在受限命名空间中执行 LLM 生成的代码，返回其 stdout。只允许 import 白名单内的模块。"""
    allowed = {"re": re, "json": json, "math": math}

    def safe_import(name, *args, **kwargs):
        if name in allowed:
            return allowed[name]
        raise ImportError(f"import {name} 不被允许")

    env = {"re": re, "json": json, "math": math, "__builtins__": {"__import__": safe_import, "len": len, "range": range, "enumerate": enumerate, "float": float, "int": int, "str": str, "print": print, "sorted": sorted, "dict": dict, "list": list, "tuple": tuple, "set": set, "isinstance": isinstance, "abs": abs, "round": round, "min": min, "max": max, "sum": sum, "ValueError": ValueError, "Exception": Exception}}
    env.update(namespace)
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        exec(code, env)  # noqa: S102
    return buffer.getvalue().strip()


def gen_extract_code(agent: Agent, sample_text: str, max_attempts: int = 3) -> str:
    """让 AI 根据一个示例章节生成通用的提取代码，在示例上验证可执行后返回。代码只生成一次，复用到所有章节。"""
    prompt = f"""以下是中国上市公司年报中的财务报表文本的一个示例章节：

```
{sample_text}
```

年报的每个章节都会以 text 变量注入同一段代码执行。请编写一段通用的 Python 代码完成以下任务：
1. 从 text 中识别出所有带金额的会计科目行（如资产、负债、权益、收入、成本、利润、现金流等），科目名以文本中的原始名称为准。
2. 将金额精确换算为元（处理千分位逗号、元/万元/亿元等单位、(123) 或 -123 负数形式），保留原始精度，不要四舍五入。
3. 同名科目出现多期数据时，只取最新一期。
4. 文本已通过 text 变量注入，代码中绝对不要重新定义或内嵌 text；禁止访问文件或网络；只允许 import re/json/math（已注入，直接用名字即可，不必 import）。
5. 最后将结果以 JSON 对象打印到 stdout：key 为科目名（str），value 为金额（number）。找不到任何金额时打印 {{}}。

只输出代码本身，不要输出任何解释或 markdown 代码块标记。"""
    messages = [{"role": "user", "content": prompt}]
    for attempt in range(max_attempts):
        code = agent.chat(messages).strip()
        code = re.sub(r"^```(?:python)?\s*|\s*```$", "", code, flags=re.MULTILINE)
        messages.append({"role": "assistant", "content": code})
        try:
            data = json.loads(run_code(code, {"text": sample_text}))
            assert all(isinstance(k, str) and isinstance(v, (int, float)) for k, v in data.items()), "提取结果必须是 科目名->数值 的 JSON"
            return code
        except Exception as e:
            messages.append({"role": "user", "content": f"代码执行报错：{e!r}。请修正代码后重新输出完整代码，仍然只输出代码本身。"})
    raise RuntimeError(f"提取代码 {max_attempts} 次尝试均失败")


def extract_sections(code: str, sections: list) -> tuple[dict[str, float], list[int]]:
    """用同一份代码提取所有章节，返回合并的科目数据和执行失败的章节下标。"""
    data: dict[str, float] = {}
    failed = []
    for i, s in enumerate(sections):
        try:
            result = json.loads(run_code(code, {"text": s.text}))
            assert all(isinstance(k, str) and isinstance(v, (int, float)) for k, v in result.items())
            data.update(result)
        except Exception:
            failed.append(i)
    return data, failed


def plan_checks(agent: Agent, data: dict[str, float]) -> list[dict]:
    """让 AI 基于实际提取到的科目名，设计会计恒等式和财务比率的校验表达式，Python 只负责求值。"""
    prompt = f"""已用程序从年报财务报表中精确提取出以下科目及金额（单位：元）：

科目列表：{json.dumps(sorted(data.keys()), ensure_ascii=False)}

请设计校验项，每个校验项包含：
- description: 校验含义描述
- lhs / rhs: 左右两边的计算表达式，只能使用上述科目名作为变量名，支持 + - * / ()
- tolerance: 允许的绝对误差（元），建议 1

需要覆盖：会计恒等式（资产=负债+权益、净利润=利润总额-所得税等，科目名以上述列表为准，找不到对应科目则跳过该校验）和常用财务比率（比率类 lhs 为分子表达式、rhs 为分母表达式）。

以 JSON 数组输出校验项。"""
    check_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "checks",
            "schema": {
                "type": "object",
                "properties": {
                    "checks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "description": {"type": "string"},
                                "lhs": {"type": "string"},
                                "rhs": {"type": "string"},
                                "tolerance": {"type": "number"},
                            },
                            "required": ["description", "lhs", "rhs", "tolerance"],
                        },
                    },
                },
                "required": ["checks"],
            },
        },
    }
    planned = json.loads(agent.chat(
        [{"role": "user", "content": prompt}],
        response_format=check_format,
    ))
    return planned["checks"]


def run_checks(data: dict[str, float], checks: list[dict]) -> list[dict]:
    """Python 精确求值 AI 设计的校验表达式，返回校验结果。"""
    results = []
    for check in checks:
        try:
            lhs = eval(check["lhs"], {"__builtins__": {}}, dict(data))
            rhs = eval(check["rhs"], {"__builtins__": {}}, dict(data))
        except (NameError, KeyError, TypeError, ZeroDivisionError):
            continue
        diff = lhs - rhs
        results.append({
            "description": check["description"],
            "lhs": check["lhs"],
            "rhs": check["rhs"],
            "lhs_value": lhs,
            "rhs_value": rhs,
            "passed": abs(diff) <= check["tolerance"],
        })
    return results


def analyze(agent: Agent, question: str, data: dict[str, float], check_results: list[dict]) -> str:
    """LLM 只负责基于已验证的精确数字做定性分析和回答。"""
    prompt = f"""问题：{question}

以下是从年报财务报表中用 Python 程序精确提取并校验过的数据（单位：元）：

原始数据：
{json.dumps(data, ensure_ascii=False, indent=2)}

校验结果：
{chr(10).join(f"[{'通过' if r['passed'] else '不通过'}] {r['description']}: {r['lhs']} = {r['lhs_value']:,.2f}, {r['rhs']} = {r['rhs_value']:,.2f}" for r in check_results)}

请基于以上精确数据回答问题，要求：
1. 只能使用上述数据，不得推测或引入外部数据。
2. 引用数字时保留原始精度。
3. 如有校验不通过项，必须指出并分析可能原因。"""

    system = "你是一个价值投资分析师，擅长基于精确的财务数据做分析。所有数字已经过程序精确提取和校验，直接引用即可。"
    return agent.chat([{"role": "system", "content": system}, {"role": "user", "content": prompt}]).strip()


if __name__ == "__main__":
    DIM, GREEN, YELLOW, CYAN, RED, RESET = "\033[2m", "\033[32m", "\033[33m", "\033[36m", "\033[31m", "\033[0m"

    agent = Agent(trace=True)

    report = AnnualReport(Path(__file__).parent / "test_annual.md")
    sections = list(report.section_chunks_aggregate_max_token(max_tokens=8000))

    # 让 AI 从章节标题中选出包含财务报表数据的章节
    section_titles = "\n".join(f"{i}: {' › '.join(s.titles)}" for i, s in enumerate(sections))
    select_prompt = f"""以下是一份年报分成的 {len(sections)} 个章节的标题：

{section_titles}

财务数据分析需要用到财务报表数据（资产负债表、利润表、现金流量表及其附注）。请选出包含这类数据的章节编号，以 JSON 格式输出。"""
    select_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "section_selection",
            "schema": {
                "type": "object",
                "properties": {
                    "indices": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["indices"],
            },
        },
    }
    selected = json.loads(agent.chat(
        [{"role": "user", "content": select_prompt}],
        response_format=select_format,
    ))["indices"]
    fin_sections = [sections[i] for i in selected]
    print(f"{DIM}AI 选中了 {len(fin_sections)}/{len(sections)} 个章节：{selected}{RESET}")

    # 精确提取：AI 只生成一次代码，Python 复用到所有章节（LLM 不转录数字）
    code = gen_extract_code(agent, fin_sections[0].text)
    data, failed = extract_sections(code, fin_sections)
    if failed:
        print(f"{YELLOW}! 以下章节提取失败已跳过：{failed}{RESET}")
    print(f"{GREEN}✓ 提取到 {len(data)} 个科目：{sorted(data.keys())}{RESET}")

    # 让 AI 设计校验项，Python 精确求值
    checks = plan_checks(agent, data)
    check_results = run_checks(data, checks)
    for r in check_results:
        status = f"{GREEN}✓{RESET}" if r["passed"] else f"{RED}✗{RESET}"
        detail = f"{r['lhs']} = {r['lhs_value']:,.2f}  vs  {r['rhs']} = {r['rhs_value']:,.2f}"
        print(f"{status} {r['description']}  {DIM}({detail}){RESET}")

    questions = [
        "这家公司的财务风险如何？偿债能力怎么样？",
        "盈利质量如何？净利润有没有现金流支撑？",
    ]
    for question in questions:
        print(f"\n{CYAN}■ 问题：{question}{RESET}")
        print(analyze(agent, question, data, check_results))
        print("─" * 60)
