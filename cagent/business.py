import argparse
import json
import textwrap
from pathlib import Path

from cagent.agent import Agent
from cagent.chunk import Toc, split_pages

agent = Agent(trace=True)


def pick_chapters(agent: Agent, chapters: list[dict]) -> list[dict]:
    """给 agent 章节列表，让它选出描述这门生意要精读的章。"""
    listing = "\n".join(f'<chapter index="{i}">\n{c["title"]}\n</chapter>' for i, c in enumerate(chapters))
    prompt = textwrap.dedent(f"""
        <requirements>
        你在通读一份上市公司年报的目录。要描述这门生意怎么经营（卖什么、怎么生产、怎么卖、钱怎么收），需要精读哪些章？
        从列表里选，返回章节序号。
        </requirements>

        <chapters>
        {listing}
        </chapters>
    """).strip()
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "chapters",
            "schema": {
                "type": "object",
                "properties": {"indexes": {"type": "array", "items": {"type": "integer"}}},
                "required": ["indexes"],
            },
        },
    }
    picks = json.loads(
        agent.chat(
            messages=[{"role": "user", "content": prompt}],
            response_format=response_format,
            temperature=0.3,
        )
    )["indexes"]
    for i in picks:
        if not 0 <= i < len(chapters):
            raise ValueError(f"无效章节序号: {i}")
    return [chapters[i] for i in picks]


def read_picks(pages: list[str], ranges: list[tuple[int, int]]) -> str:
    """按章节页码范围取原文，包成 <pages> 窗口。"""
    idx = [i for lo, hi in ranges for i in range(lo, hi + 1)]
    return "\n".join(f'<page index="{i}">\n{pages[i - 1]}\n</page>' for i in idx)


def describe_business(agent: Agent, window: str) -> str:
    """读 agent 挑的页，描述这门生意怎么经营。"""
    prompt = textwrap.dedent(f"""
        <requirements>
        阅读下面的年报页面，描述这门生意是怎么经营的：卖什么产品、怎么生产出来、怎么卖给客户、钱怎么收回来。
        只根据页面内容回答。
        </requirements>

        <pages>
        {window}
        </pages>
    """).strip()
    return agent.chat(messages=[{"role": "user", "content": prompt}], temperature=0.3)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="商业模式分析 agent")
    parser.add_argument("-i", "--input", default=str(Path(__file__).parent / "test_annual.md"), help="年报 md 路径")
    args = parser.parse_args()

    md = Path(args.input)
    pages = split_pages(md.read_text(encoding="utf-8"))
    toc = Toc(md)
    picks = pick_chapters(agent, toc.chapters)
    print(f"选中章: {[c['title'] for c in picks]}")
    print(describe_business(agent, read_picks(pages, [toc.range(c) for c in picks])))
