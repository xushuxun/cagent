"""wiki agent：知识层 writer 的入口。每个模块是独立文件 wiki_<topic>.py，在此直接 import 汇总。

端到端跑通 business / products / decisions 三个 writer，产物为各模块 Markdown 与一份
汇总完整 HTML（output/wiki/<market>/<stock>/index.html）。

用法：
    uv run cagent/wiki_agent.py --stock 601633 --market cn
    uv run cagent/wiki_agent.py --stock 601633 --market cn --force
"""

import argparse
import logging
import sys
from pathlib import Path

from cagent.agent import Agent
from cagent.wiki_business import gen_business
from cagent.wiki_decisions import gen_decisions
from cagent.wiki_finance import gen_finance
from cagent.wiki_products import gen_products
from cagent.wiki_render import render_doc

logger = logging.getLogger(__name__)

# 模块顺序即页面栏目顺序；每个 gen 输出的 markdown 自带一级标题
_MODULES = [
    ("business", gen_business),
    ("products", gen_products),
    ("decisions", gen_decisions),
    ("finance", gen_finance),
]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S", stream=sys.stderr)
    parser = argparse.ArgumentParser(description="wiki agent：端到端生成研究报告")
    parser.add_argument("--stock", default="601633")
    parser.add_argument("--market", default="cn", choices=["cn", "hk"])
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1] / ".cagent"))
    parser.add_argument("--force", action="store_true", help="重跑全部模块")
    args = parser.parse_args()

    output_dir = Path(__file__).resolve().parents[1] / "output" / "wiki" / f"{args.market}" / f"{args.stock}"
    output_dir.mkdir(parents=True, exist_ok=True)

    root = Path(args.root)
    agent = Agent(trace=True)

    docs = []
    for name, gen in _MODULES:
        md_path = output_dir / f"{name}.md"
        if md_path.exists() and not args.force:
            doc = md_path.read_text(encoding="utf-8")
            logger.info(f"复用: {md_path.name}")
        else:
            logger.info(f"=== {name} ===")
            doc = gen(agent, args.stock, args.market, root)
            md_path.write_text(doc, encoding="utf-8")
        docs.append(doc)

    html = render_doc(docs, title=f"{args.stock}.{args.market} · 公司研究", ticker=args.stock)
    html_path = output_dir / "index.html"
    html_path.write_text(html, encoding="utf-8")
    logger.info(f"渲染: {html_path}")


if __name__ == "__main__":
    main()
