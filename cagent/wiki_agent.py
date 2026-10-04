"""wiki agent：知识层 writer 的入口。每个模块是独立文件 wiki_<topic>.py，在此汇总注册。

用法：
    uv run cagent/wiki_agent.py --module decisions --stock 601633 --market cn

模块产物落位 .cagent/<market>/<stock>/knowledge/fy<year>/<topic>.json：
蒸馏描述 + 定位索引（章节、页码范围），不抄录原文。已存在则跳过，--force 重跑，
--years 2024,2025 只跑指定年份。
"""

import argparse
import logging
import sys
from pathlib import Path

from cagent import wiki_business, wiki_decisions, wiki_governance, wiki_products
from cagent.agent import Agent

MODULES = {
    "business": wiki_business.gen_business,
    "products": wiki_products.gen_products,
    "governance": wiki_governance.gen_governance,
    "decisions": wiki_decisions.gen_decisions,
}

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S", stream=sys.stderr)
    parser = argparse.ArgumentParser(description="wiki agent：知识层 writer 入口")
    parser.add_argument("--module", required=True, choices=sorted(MODULES))
    parser.add_argument("--stock", default="601633")
    parser.add_argument("--market", default="cn", choices=["cn", "hk"])
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1] / ".cagent"))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--years", help="只跑指定年份，逗号分隔，如 2024,2025")
    args = parser.parse_args()
    years = [int(y) for y in args.years.split(",")] if args.years else None
    agent = Agent(trace=True)
    MODULES[args.module](agent, args.stock, args.market, Path(args.root), args.force, years)
