"""decisions 模块：公司历年经营决策 writer

逐年两段蒸馏：先提取管理层对行业大环境的判断，再提取当年的重大经营决策，
代码拼接为一张三列 markdown 表格，产物
output/wiki/<market>/<stock>/decisions.md。只产内容，不管 UI；页面渲染见 wiki_render.py。

用法：
    uv run cagent/wiki_decisions.py --stock 601633 --market cn
    uv run cagent/wiki_decisions.py --stock 601633 --market cn --force
    uv run cagent/wiki_decisions.py --stock 601633 --market cn --render
"""

import argparse
import logging
import sys
import textwrap
from pathlib import Path

from cagent.agent import Agent
from cagent.chunk import Toc, report_year
from cagent.wiki_render import render_doc

logger = logging.getLogger(__name__)


_judge_examples = [
    "大模型爆发带动 GPU 市场高速增长，出口管制与自主可控使国产 AI 芯片迎来黄金发展期，GPU 与 ASIC 将长期共存",
    "开源大模型快速普及，人工智能迈入规模化落地阶段，编程将迎来 L4 至 L5 级别智能，应用层面临创新窗口期",
]

_decide_examples = [
    """发布全国产工艺曦云 C600 并推进风险量产
布局 1+6+X 生态，推进智算集群落地
研发投入 10.27 亿元，占营收 62.49%""",
    """发布 M2 系列三款语言模型
发布视频模型 Hailuo 2.3 与语音模型 Speech 2.6
内部推行 Agent 实习生，推出 MiniMax Agent Workspace""",
]


def _judge_prompt() -> str:
    return textwrap.dedent(f"""
        <requirement>
        你在阅读一份上市公司年报，提取管理层对行业大环境的判断，输出几句话

        - 只写原文有依据的内容，禁止编造任何原文不存在的细节
        - 控制在100字内，语言用简体中文
        </requirement>

        <examples>
        {"\n".join(f"<example>{e}</example>" for e in _judge_examples)}
        </examples>""").strip()


def _decide_prompt() -> str:
    return textwrap.dedent(f"""
        <requirement>
        你在阅读一份上市公司年报，提取当年的重大经营决策，输出几句话

        - 只记管理层当年的行动与投入（发布、布局、投产、研发、收购等），不记效果、成绩与评价
        - 只写原文有依据的内容，禁止编造任何原文不存在的细节
        - 控制在100字内，语言用简体中文
        </requirement>

        <examples>
        {"\n".join(f"<example>{e}</example>" for e in _decide_examples)}
        </examples>""").strip()


_pick_task = "你在阅读一份上市公司年报的目录，找出管理层对行业大环境的判断与当年的重大经营决策。需要阅读哪些章节？"


def gen_decisions(agent: Agent, stock: str, market: str, root: Path) -> str:
    derived = root / market / stock / "derived"
    mds = sorted(derived.glob("*.md"))
    if not mds:
        raise SystemExit(f"{derived} 没有年报 md，先跑 ocr/cli.py")
    md_by_year = {report_year(agent, p): p for p in mds}

    rows = []
    for year, md_path in sorted(md_by_year.items()):
        toc = Toc(agent, md_path)
        logger.info(f"阅读: {md_path.name}")

        picked = toc.pick_chapters(toc.chapters, _pick_task)
        logger.info(f"选中章节: {[toc.chapters[i]['title'] for i in picked]}")
        chunks = [chunk for i in picked for chunk in toc.chapter_text_chunk(i)]

        logger.info(f"{year} gen …")
        judge = agent.chat_reduce_chunks(_judge_prompt(), chunks).strip().replace("\n", "<br>")
        decide = agent.chat_reduce_chunks(_decide_prompt(), chunks).strip().replace("\n", "<br>")
        if not judge or not decide:
            raise ValueError(f"{year} decisions 分析异常")
        rows.append(f"| {year} | {judge} | {decide} |")

    header = "| 年份 | 管理层对行业大环境的判断 | 当年的重大经营决策 |\n| --- | --- | --- |"
    return header + "\n" + "\n".join(rows) + "\n"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S", stream=sys.stderr)
    parser = argparse.ArgumentParser(description="decisions 模块：蒸馏历年经营决策")
    parser.add_argument("--stock", default="601633")
    parser.add_argument("--market", default="cn", choices=["cn", "hk"])
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1] / ".cagent"))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--render", action="store_true", help="生成后渲染 decisions.html")
    args = parser.parse_args()

    output_dir = Path(__file__).resolve().parents[1] / "output" / "wiki" / f"{args.market}" / f"{args.stock}"
    output_dir.mkdir(parents=True, exist_ok=True)

    md_output_path = output_dir / "decisions.md"
    if md_output_path.exists() and not args.force:
        doc = md_output_path.read_text(encoding="utf-8")

    else:
        root = Path(args.root)
        agent = Agent(trace=True)
        doc = gen_decisions(agent, args.stock, args.market, root)
        md_output_path.write_text(doc, encoding="utf-8")

    print(doc)

    if args.render:
        html_output_path = output_dir / "decisions.html"
        html_output_path.write_text(render_doc(doc, title=f"{args.stock} · 历年经营决策", ticker=args.stock, layout="decisions"), encoding="utf-8")
        logger.info(f"渲染: {html_output_path}")


if __name__ == "__main__":
    main()
