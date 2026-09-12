import json
import textwrap
from pathlib import Path

from cagent.agent import Agent
from cagent.doc import AnnualReport, Section


def answer(agent: Agent, question: str, section: Section) -> str | None:
    """判断 section 与问题是否相关，相关则基于正文回答并与 previous_answer 合并，否则跳过（返回 previous_answer）。"""
    titles = "\n".join(f"- {t}" for t in section.titles)
    relevance_prompt = textwrap.dedent(f"""
        问题：{question}

        以下是年报中的部分章节标题：
        {titles}

        请判断该章节的内容是否可能有助于回答问题，并以 JSON 格式输出判断结果。
    """).strip()
    relevance_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "relevance",
            "schema": {
                "type": "object",
                "properties": {"relevant": {"type": "boolean"}},
                "required": ["relevant"],
            },
        },
    }
    relevance = json.loads(agent.chat(
        [{"role": "user", "content": relevance_prompt}],
        response_format=relevance_format,
    ))
    if not relevance["relevant"]:
        return None

    prompt = textwrap.dedent("""
        问题：{question}

        以下是年报章节内容
        {content}

        请基于上述内容回答问题，要求：
        1. 只使用章节内容中的事实，不要推测
        2. 如果章节内容中没有相关信息，请直接回答“年报中没有找到相关内容”，不要编造答案
    """).strip().format(question=question, content=section.text)


    return agent.chat([{"role": "system", "content": "你是一个价值投资助手"}, {"role": "user", "content": prompt}]).strip()

def merge_answer(agent: Agent, question: str, answers: list[str]) -> str:
    if not answers:
        return ""

    prompt = textwrap.dedent("""
        问题：{question}

        现有 {count} 个回答，请将它们合并为一个完整的回答，要求：
        1. 去掉与问题无关、不重要、参考意义不大的内容。
        2. 如果回答中有矛盾或者重复，删掉，只保留精简自恰的内容。
        各个回答如下：
        {answers}
        请输出合并后的完整回答。
    """).strip().format(
        question=question,
        count=len(answers),
        answers="\n".join(f"{i+1}. {a}" for i, a in enumerate(answers)),
    )
    return agent.chat([{"role": "system", "content": "你是一个价值投资助手"}, {"role": "user", "content": prompt}]).strip()


if __name__ == "__main__":

    DIM, GREEN, YELLOW, CYAN, RESET = "\033[2m", "\033[32m", "\033[33m", "\033[36m", "\033[0m"

    def short_title(titles: list[str], width: int = 70) -> str:
        text = " › ".join(titles)
        return text if len(text) <= width else text[: width - 1] + "…"

    agent = Agent()

    questions = [
        "商业模式介绍",
        "管理层认知、判断与决策",
    ]

    report = AnnualReport(Path(__file__).parent / "test_annual.md")
    sections = list(report.section_chunks_aggregate_max_token(max_tokens=2000))
    print(f"{DIM}共 {len(sections)} 个章节，{len(questions)} 个问题{RESET}")

    for question in questions:
        print(f"\n{CYAN}■ 问题：{question}{RESET}")
        answers = []
        for i, section in enumerate(sections, 1):
            prefix = f"{DIM}[{i:>3}/{len(sections)}]{RESET}"
            section_answer = answer(
                agent=agent,
                question=question,
                section=section,
            )
            if section_answer is None:
                print(f"{prefix} {DIM}✗ 跳过  {short_title(section.titles)}{RESET}")
            else:
                answers.append(section_answer)
                print(f"{prefix} {GREEN}✓ 采用  {short_title(section.titles)}{RESET}")

        if answers:
            print(f"\n{YELLOW}■ 合并 {len(answers)}/{len(sections)} 个章节回答 ...{RESET}")
            final_answer = merge_answer(agent, question, answers)
        else:
            final_answer = ""

        print(f"\n{YELLOW}■ 最终回答{RESET} {DIM}(参考了 {len(answers)}/{len(sections)} 个章节){RESET}")
        print(final_answer or f"{DIM}年报中没有找到相关内容{RESET}")
        print("─" * 60)

