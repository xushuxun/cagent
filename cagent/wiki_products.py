"""products 模块：主要产品 writer

逐年整理主要产品的结构与规模，产物落位 knowledge/fy<year>/products.json，
各年文档在 reader 侧按年份分栏展示。

用法：
    uv run cagent/wiki_products.py --stock 601633 --market cn
    uv run cagent/wiki_products.py --stock 601633 --market cn --force
"""

import argparse
import json
import logging
import sys
import textwrap
from pathlib import Path

from cagent.agent import Agent
from cagent.chunk import Toc, report_year

logger = logging.getLogger(__name__)


_product_examples = [
    {
        "pages": """2025年，公司实现整车销售382,146辆，同比增长11.2%。分车型看，SUV销量146,208辆，同比增长18.4%；皮卡销量83,052辆；轻卡销量91,377辆；轻客销量43,815辆。新能源车型全年销量52,040辆，同比增长96.3%，占整车销量的13.6%。""",
        "output": """<table>
<tr><td>SUV</td><td class="n">14.62</td></tr>
<tr><td>皮卡</td><td class="n">8.31</td></tr>
<tr><td>轻卡</td><td class="n">9.14</td></tr>
<tr><td>轻客</td><td class="n">4.38</td></tr>
<tr class="hl"><td>新能源合计</td><td class="n">5.20</td></tr>
</table>""",
    },
    {
        "pages": """集团拥有荣威、MG、智己、大通等品牌。2025年集团整车批发销量401.3万辆，同比增长12.3%；其中新能源汽车销量107.3万辆，同比增长24.5%；海外市场销量92.8万辆。智己汽车全年交付65,017辆，同比增长71%。荣威、MG 两个乘用车品牌未单独披露销量。""",
        "output": """<table>
<tr><td>荣威·MG</td><td class="src">未单列</td></tr>
<tr><td>智己</td><td class="n">6.50</td></tr>
<tr><td>大通</td><td class="src">未单列</td></tr>
<tr class="hl"><td>新能源合计</td><td class="n">107.3</td></tr>
<tr class="hl"><td>海外合计</td><td class="n">92.8</td></tr>
</table>""",
    },
]



def _product_prompt() -> str:
    return textwrap.dedent(f"""
        <requirement>
        你在阅读一份上市公司年报，整理当年主要品牌/产品线的销量，输出一个 HTML 表格：每行一个品牌或产品线，两列（名称、销量），
        销量以万辆计、保留一位小数，数字列加 class="n"；未单独披露销量的填 <td class="src">未单列</td>；
        按披露的口径整理，分车型与分品牌都有时优先有销量数字的口径；新能源、海外等关键合计放末行并加 class="hl"。不要表头，不要备注。

        - 禁止编撰；全文用简体中文
        </requirement>

        <examples>
        {"\n".join(f"<example>\\n<pages>\\n{x['pages']}\\n</pages>\\n<output>\\n{x['output']}\\n</output>\\n</example>" for x in _product_examples)}
        </examples>""").strip()


_pick_task = "你在通读一份上市公司年报的目录。了解主要产品与品牌结构：分车型或分品牌销量（产销量情况分析表）、品牌定位，需要阅读哪些章节？"


def gen_products(agent: Agent, stock: str, market: str, root: Path, force: bool, years: list[int] | None = None) -> list[tuple[int, str]]:
    derived = root / market / stock / "derived"
    mds = sorted(derived.glob("*.md"))
    if not mds:
        raise SystemExit(f"{derived} 没有年报 md，先跑 ocr/cli.py")
    md_by_year = {report_year(agent, p): p for p in mds}
    tocs = {year: Toc(agent, p) for year, p in md_by_year.items()}

    knowledge_dir = root / market / stock / "knowledge"
    docs = []
    for year, toc in sorted(tocs.items()):
        if years and year not in years:
            continue
        out_path = knowledge_dir / md_by_year[year].stem / "products.json"
        if out_path.exists() and not force:
            logger.info(f"跳过（已存在）: {out_path}")
            docs.append((year, json.loads(out_path.read_text(encoding="utf-8"))["doc"]))
            continue

        logger.info(f"{year} …")
        picked = toc.pick_chapters(toc.chapters, _pick_task)
        logger.info(f"选中章: {[toc.chapters[i]['title'] for i in picked]}")
        chunks = [chunk for i in picked for chunk in toc.chapter_text_chunk(i)]

        doc = agent.chat_reduce_chunks(_product_prompt(), chunks)
        if not doc.strip():
            raise ValueError(f"{year} products 分析异常")

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps({"year": year, "topic": "products", "source": toc.gen_source(picked), "doc": doc}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        logger.info(f"输出: {out_path}")
        docs.append((year, doc))
    return docs


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S", stream=sys.stderr)
    parser = argparse.ArgumentParser(description="products 模块：蒸馏主要产品，产物 knowledge/fy<year>/products.json")
    parser.add_argument("--stock", default="601633")
    parser.add_argument("--market", default="cn", choices=["cn", "hk"])
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1] / ".cagent"))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--years", help="只跑指定年份，逗号分隔，如 2024,2025")
    args = parser.parse_args()
    years = [int(y) for y in args.years.split(",")] if args.years else None

    agent = Agent(trace=True)
    gen_products(agent, args.stock, args.market, Path(args.root), args.force, years)



if __name__ == "__main__":
    main()
