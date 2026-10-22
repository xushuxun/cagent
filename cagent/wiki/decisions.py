"""历年经营决策：逐年读年报原文，提管理层对行业的判断与当年的重大决策

读最近五年年报的管理层讨论与分析章节，每年两小节：管理层对行业大环境的判断、
当年的重大经营决策。只写年报披露的内容；不标页码、不写出处。

    uv run -m cagent.wiki.decisions --stock 601633 --market cn
"""

import argparse
import logging
import sys
import textwrap
from pathlib import Path

from cagent.core.agent import Agent
from cagent.core.chunk import Toc

logger = logging.getLogger(__name__)

_WINDOW = 5  # 最近五年一个滚动窗口


def _report_year(agent: Agent, md_path: Path) -> int:
    """报告年度问模型：年报开头（封面/标题）必含年度信息。"""
    head = md_path.read_text(encoding="utf-8")[:1000]
    prompt = textwrap.dedent(f"""
        <requirement>
        从年报开头判断这份年报的报告年度是哪一年，只返回整数年份
        </requirement>

        <head>
        {head}
        </head>
    """)
    schema = {"type": "object", "properties": {"year": {"type": "integer"}}, "required": ["year"]}
    return agent.chat_json(prompt, schema)["year"]

def _gen_prompt(task: str) -> str:
    return textwrap.dedent(
        f"""
        <requirement>
        {task}

        话术要求：
        - 去掉任何管理层吹嘘话术，同时删掉业绩汇报
        - 用简单的陈述句，语言极度简洁凝炼
        - 不标页码、不写出处，正文直接叙述
        - 用简体中文，单个段落，不要换行，raw text
        </requirement>
        """
    ).strip()



def _gen_one_year(agent: Agent, md_path: Path) -> str:
    """读一份年报，写一年的两小节；两阶段生成共享 chunk 前缀。"""
    toc = Toc(agent, md_path)
    chunks = toc.get_chapters_chunks(task="为了提取管理层对行业的判断与经营决策，找出相关章节，除了财务报表章节，都尽量召回")
    prompt_judge = _gen_prompt("你在阅读一份上市公司年报，提取管理层对行业大环境的判断。全文控制在 200 字以内。")
    prompt_decide = _gen_prompt("你在阅读一份上市公司年报，提取管理层当年的重大经营决策。全文控制在 200 字以内。")
    judge = agent.chat_distill(prompt_judge, chunks)
    decide = agent.chat_distill(prompt_decide, chunks)
    if not judge or not decide:
        raise ValueError(f"{md_path.name} 生成异常：空输出")
    cell = lambda text: "<br>".join(line for line in text.splitlines() if line.strip())
    return f"| 管理层对行业大环境的判断 | 当年的重大经营决策 |\n| --- | --- |\n| {cell(judge)} | {cell(decide)} |\n"


def _gen(agent: Agent, stock: str, market: str, root: Path) -> str:
    derived = root / market / stock / "derived"
    mds = sorted(derived.glob("*.md"))
    if not mds:
        raise SystemExit(f"{derived} 没有年报 md，先跑 cagent.ocr.cli")

    parts = ["# 历年经营决策\n"]
    for md_path in mds[-_WINDOW:]:
        logger.info(f"{md_path.name}")
        parts.append(f"## {_report_year(agent, md_path)}\n")
        parts.append(_gen_one_year(agent, md_path))
    return "\n".join(parts).strip() + "\n"


def build(agent: Agent, stock: str, market: str, root: Path, output_dir: Path, force: bool = False) -> str:
    """写历年经营决策；产物在就复用。返回整节 markdown（含一级标题）。"""
    target = output_dir / "decisions.md"
    if target.exists() and not force:
        logger.info(f"reuse {target.name}")
        return target.read_text(encoding="utf-8")
    doc = _gen(agent, stock, market, root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(doc, encoding="utf-8")
    logger.info(f"write {target}")
    return doc


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S", stream=sys.stderr)
    parser = argparse.ArgumentParser(description="历年经营决策")
    parser.add_argument("--stock", default="601633")
    parser.add_argument("--market", default="cn", choices=["cn", "hk"])
    parser.add_argument("--root", type=Path, default=Path(".cagent"))
    parser.add_argument("--out", type=Path, default=Path("output/wiki"))
    parser.add_argument("--force", action="store_true", help="重跑（默认复用已有小节）")
    args = parser.parse_args()

    llm = Agent(trace=True)
    build(llm, args.stock, args.market, args.root, args.out / args.market / args.stock, force=args.force)


if __name__ == "__main__":
    main()
