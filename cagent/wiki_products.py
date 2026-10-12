"""products 模块：产品与销量 writer

逐年整理主要产品/品牌结构与销量，每年一张表，产物为 Markdown 文档
output/wiki/<market>/<stock>/products.md。只产内容，不管 UI；页面渲染见 wiki_render.py。

用法：
    uv run cagent/wiki_products.py --stock 601633 --market cn
    uv run cagent/wiki_products.py --stock 601633 --market cn --force
    uv run cagent/wiki_products.py --stock 601633 --market cn --render
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


_product_examples = [
    """
| 产品 | 销售量（片） |
| --- | ---: |
| 训推一体 GPU 板卡 | 33,649 |
| 智算推理 GPU 板卡 | 4,946 |
""",
    """
| 业务线 | 收入（百万美元） | 占比 |
| --- | ---: | ---: |
| AI 原生产品 | 53.1 | 67.2% |
| 开放平台及企业服务等 | 26.0 | 32.8% |
""",
    """
| 技术平台 | 收入（千美元） | 占比 |
| --- | ---: | ---: |
| 分立器件 | 557,893 | 34.2% |
| 嵌入式非易失性存储器 | 459,104 | 28.2% |
| 逻辑与射频 | 272,053 | 16.7% |
| 模拟与电源管理 | 250,466 | 15.4% |
| 独立式非易失性存储器 | 88,796 | 5.4% |
| **合计** | **1,630,754** | **100.0%** |
""",
]


def gen_products(agent: Agent, stock: str, market: str, root: Path) -> str:
    derived = root / market / stock / "derived"
    mds = sorted(derived.glob("*.md"))
    if not mds:
        raise SystemExit(f"{derived} 没有年报 md，先跑 ocr/cli.py")
    md_by_year = {report_year(agent, p): p for p in mds}

    sections = []
    for year, md_path in sorted(md_by_year.items()):
        toc = Toc(agent, md_path)
        logger.info(f"阅读: {md_path.name}")

        picked = toc.pick_chapters(toc.chapters,  "你在阅读一份上市公司年报的目录。获取公司的产品服务以及销量，需要阅读哪些章节？不要看财务报表")
        logger.info(f"选中章节: {[toc.chapters[i]['title'] for i in picked]}")
        chunks = [chunk for i in picked for chunk in toc.chapter_text_chunk(i)]

        logger.info(f"{year} gen …")

        prompt = textwrap.dedent(f"""
            <requirement>
            你在阅读一份上市公司年报，整理该年产品销量或规模，输出一张 markdown 表
            
            - 只抄录年报内容数字，严禁自己统计计算和编造，合计占比也禁止自己算，年报中没有则留空
            - 语言用简体中文
            </requirement>

            <examples>
            {"\n".join(f"<example>{e}</example>" for e in _product_examples)}
            </examples>""").strip()
        table = agent.chat_reduce_chunks(prompt, chunks)
        if not table.strip():
            raise ValueError(f"{year} products 分析异常")

        sections.append(f"#### {year}\n\n{table.strip()}")
    return "\n\n".join(sections) + "\n"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S", stream=sys.stderr)
    parser = argparse.ArgumentParser(description="products 模块：提取产品与销量")
    parser.add_argument("--stock", default="601633")
    parser.add_argument("--market", default="cn", choices=["cn", "hk"])
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1] / ".cagent"))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--render", action="store_true", help="生成后渲染 products.html")
    args = parser.parse_args()

    output_dir = Path(__file__).resolve().parents[1] / "output" / "wiki" / f"{args.market}" / f"{args.stock}"
    output_dir.mkdir(parents=True, exist_ok=True)

    md_output_path = output_dir / "products.md"
    if md_output_path.exists() and not args.force:
        doc = md_output_path.read_text(encoding="utf-8")

    else:
        root = Path(args.root)
        agent = Agent(trace=True)
        doc = gen_products(agent, args.stock, args.market, root)
        md_output_path.write_text(doc, encoding="utf-8")

    print(doc)

    if args.render:
        html_output_path = output_dir / "products.html"
        html_output_path.write_text(render_doc(doc, title=f"{args.stock} · 产品与销量", ticker=args.stock, layout="columns"), encoding="utf-8")
        logger.info(f"渲染: {html_output_path}")


if __name__ == "__main__":
    main()
