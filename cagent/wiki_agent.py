"""wiki agent：知识层 writer 的入口。每个模块是独立文件 wiki_<topic>.py，在此直接 import 汇总。

用法：
    uv run cagent/wiki_agent.py --module decisions --stock 601633 --market cn
    uv run cagent/wiki_agent.py --module governance --stock 601633 --market cn --force

模块产物落位 .cagent/<market>/<stock>/knowledge/<年报md同名目录>/<topic>.json：
蒸馏 Markdown 文档 + 定位索引（章节、页码范围），不抄录原文。gen 幂等（已存在则跳过，
--force 重跑，--years 限定年份）。
"""

import argparse
import logging
import sys
from pathlib import Path

from cagent import wiki_business, wiki_capital, wiki_decisions, wiki_products
from cagent.agent import Agent

logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S", stream=sys.stderr)
    parser = argparse.ArgumentParser(description="wiki agent：知识层 writer 入口")
    parser.add_argument("--stock", default="601633")
    parser.add_argument("--market", default="cn", choices=["cn", "hk"])
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1] / ".cagent"))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--years", help="只跑指定年份，逗号分隔，如 2024,2025（仅 decisions 支持）")
    args = parser.parse_args()
    years = [int(y) for y in args.years.split(",")] if args.years else None

    agent = Agent(trace=True)
    root = Path(args.root)



if __name__ == "__main__":
    main()
