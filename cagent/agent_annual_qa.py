import json
import textwrap
from pathlib import Path

from cagent.agent import Agent
from cagent.doc import AnnualReport, Section

system_prompt = {
    "role": "system",
    "content": "你是一个价值投资者, 你正在阅读一篇上市公司年报，回答问题要符合价值巴菲特芒格的投资理念",
}


def is_section_relevant(agent: Agent, question: str, section: Section) -> bool:
    relevance_prompt = textwrap.dedent(f"""
        <question>
        {question}
        </question>

        根据以下年报标题列表，判断其中的内容是否有助于回答问题：

        <section-titles>
        {"\n".join(f"- {t}" for t in section.titles)}
        </section-titles>

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


def answer_with_section(agent: Agent, question: str, section: Section) -> str | None:
    prompt = textwrap.dedent(f"""
        <question>
        {question}
        </question>

        <requirements>
        只使用章节内容中的事实，不要推测，无法回答问题时留空
        </requirements>

        以下是年报章节内容：
        <section-content>
        {section.text}
        </section-content>

    """).strip()
    return agent.chat(messages=[system_prompt, {"role": "user", "content": prompt}])




def merge_answer(agent: Agent, question: str, answers: list[str]) -> str:
    if not answers:
        return ""
    if len(answers) == 1:
        return answers[0]

    prompt = textwrap.dedent(f"""
        <question>
        {question}
        </question>

        <requirements>
        现有 {len(answers)} 个回答，请将它们合并为一个完整的回答，要求：
        1. 去掉与问题无关、不重要、参考意义不大的内容。
        2. 如果回答中有矛盾或者重复，删掉，只保留精简自恰的内容。
        </requirements>

        各个回答如下：
        <answers>
        {"\n\n".join(f'<answer id="{i + 1}">\n{a}\n</answer>' for i, a in enumerate(answers))}
        </answers>

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
        "本公司的生意模式，谁在付钱、为什么付钱、不付的代价是什么、靠什么持续赚钱？",
        "管理层对宏观环境、行业趋势、公司经营的认知与判断，以及相关决策",
    ]

    report = AnnualReport(Path(__file__).parent / "test_annual.md")
    sections = list(report.section_chunks_aggregate_max_token(max_tokens=3000))
    print(f"{DIM}共 {len(sections)} 个章节，{len(questions)} 个问题{RESET}")

    for question in questions:
        print(f"\n{CYAN}■ 问题：{question}{RESET}")
        answers = []
        for i, section in enumerate(sections, 1):
            prefix = f"{DIM}[{i:>3}/{len(sections)}]{RESET}"

            is_relevant = is_section_relevant(agent, question, section)
            if not is_relevant:
                print(f"{prefix} {DIM}✗ 标题跳过  {short_title(section.titles)}{RESET}")
                continue

            section_answer = answer_with_section(agent=agent, question=question, section=section)
            if section_answer is not None:
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
