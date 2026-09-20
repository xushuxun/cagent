import json
import textwrap
from pathlib import Path

from cagent.agent import Agent
from cagent.doc import AnnualReport, Section

system_prompt = {
    "role": "system",
    "content": "你是一个价值投资助手, 回答问题时要求言简意赅，面向投资者，符合价值巴菲特芒格的投资理念",
}


def is_section_relevant(agent: Agent, question: str, section: Section) -> bool:
    relevance_prompt = textwrap.dedent(f"""
        问题：{question}

        根据年报标题列表，猜测其中某些章节内容是否可能与问题相关

        以下是年报中的部分章节标题：
        {"\n".join(f"- {t}" for t in section.titles)}

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
    relevance = json.loads(
        agent.chat(
            messages=[system_prompt, {"role": "user", "content": relevance_prompt}],
            response_format=relevance_format,
        )
    )
    return relevance["relevant"]


def answer(agent: Agent, question: str, section: Section) -> str | None:
    prompt = textwrap.dedent(f"""
        问题：{question}

        要求：
        1. 只使用章节内容中的事实，不要推测
        2. 以 JSON 格式输出回答，包含两个字段：
           - answer: 回答内容，若章节内容不足以回答问题，则为空字符串
           - sufficient: 布尔值，表示章节内容是否足以有意义回答问题

        以下是年报章节内容：
        {section.text}

    """).strip()
    answer_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "answer",
            "schema": {
                "type": "object",
                "properties": {
                    "answer": {"type": "string"},
                    "sufficient": {"type": "boolean"},
                },
                "required": ["answer", "sufficient"],
            },
        },
    }
    response = json.loads(
        agent.chat(
            messages=[system_prompt, {"role": "user", "content": prompt}],
            response_format=answer_format,
        )
    )
    answer_text = response["answer"].strip()
    if not response.get("sufficient") or not answer_text:
        return None
    return answer_text


def merge_answer(agent: Agent, question: str, answers: list[str]) -> str:
    if not answers:
        return ""
    if len(answers) == 1:
        return answers[0]

    prompt = textwrap.dedent(f"""
        问题：{question}

        现有 {len(answers)} 个回答，请将它们合并为一个完整的回答，要求：
        1. 去掉与问题无关、不重要、参考意义不大的内容。
        2. 如果回答中有矛盾或者重复，删掉，只保留精简自恰的内容。
        各个回答如下：
        {"\n\n".join(f"回答 {i + 1}:\n{a}" for i, a in enumerate(answers))}
        请输出合并后的完整回答。
    """).strip()
    return agent.chat(messages=[system_prompt, {"role": "user", "content": prompt}]).strip()


if __name__ == "__main__":
    DIM, GREEN, YELLOW, CYAN, RESET = (
        "\033[2m",
        "\033[32m",
        "\033[33m",
        "\033[36m",
        "\033[0m",
    )

    def short_title(titles: list[str], width: int = 70) -> str:
        text = " › ".join(titles)
        return text if len(text) <= width else text[: width - 1] + "…"

    agent = Agent(trace=True)

    questions = [
        # "生意模式",
        "管理层认知、判断与决策",
    ]

    report = AnnualReport(Path(__file__).parent / "test_annual.md")
    sections = list(report.section_chunks_aggregate_max_token(max_tokens=3000))
    print(f"{DIM}共 {len(sections)} 个章节，{len(questions)} 个问题{RESET}")

    for question in questions:
        print(f"\n{CYAN}■ 问题：{question}{RESET}")
        answers = []
        for i, section in enumerate(sections, 1):
            prefix = f"{DIM}[{i:>3}/{len(sections)}]{RESET}"

            # is_relevant = is_section_relevant(agent, question, section)
            # if not is_relevant:
            #     print(f"{prefix} {DIM}✗ 标题跳过  {short_title(section.titles)}{RESET}")
            #     continue

            section_answer = answer(agent=agent, question=question, section=section)
            if section_answer is None:
                print(f"{prefix} {DIM}✗ 内容跳过  {short_title(section.titles)}{RESET}")
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
