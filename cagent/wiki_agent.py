"""wiki agent：知识层 writer 的入口。每个模块是独立文件 wiki_<topic>.py，在此直接 import 汇总。

用法：
    uv run cagent/wiki_agent.py --module decisions --stock 601633 --market cn
    uv run cagent/wiki_agent.py --module governance --stock 601633 --market cn --force

模块产物落位 .cagent/<market>/<stock>/knowledge/fy<year>/<topic>.json：
蒸馏 Markdown 文档 + 定位索引（章节、页码范围），不抄录原文。gen 幂等（已存在则跳过，
--force 重跑，--years 限定年份）。
"""

import argparse
import logging
import sys
from pathlib import Path

from cagent import wiki_business, wiki_capital, wiki_decisions, wiki_governance, wiki_products
from cagent.agent import Agent

logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S", stream=sys.stderr)
    parser = argparse.ArgumentParser(description="wiki agent：知识层 writer 入口")
    parser.add_argument("--module", required=True, choices=["business", "capital", "decisions", "governance", "products"])
    parser.add_argument("--stock", default="601633")
    parser.add_argument("--market", default="cn", choices=["cn", "hk"])
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1] / ".cagent"))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--years", help="只跑指定年份，逗号分隔，如 2024,2025（仅 decisions 支持）")
    args = parser.parse_args()
    years = [int(y) for y in args.years.split(",")] if args.years else None

    agent = Agent(trace=True)
    root = Path(args.root)
    if args.module == "business":
        wiki_business.gen_business(agent, args.stock, args.market, root, args.force)
    elif args.module == "capital":
        wiki_capital.gen_capital(agent, args.stock, args.market, root, args.force)
    elif args.module == "decisions":
        wiki_decisions.gen_decisions(agent, args.stock, args.market, root, args.force, years)
    elif args.module == "governance":
        wiki_governance.gen_governance(agent, args.stock, args.market, root, args.force)
    else:
        wiki_products.gen_products(agent, args.stock, args.market, root, args.force, years)

    from cagent.wiki_render import render_module

    html_path = render_module(root, args.market, args.stock, args.module, Path("output/wiki") / f"{args.market}_{args.stock}" / f"{args.module}.html")
    logger.info(f"渲染: {html_path}")


if __name__ == "__main__":
    main()
