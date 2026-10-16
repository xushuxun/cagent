"""pipeline：一家公司跑到底（抽事实 → 装配报告）

    uv run -m cagent.pipeline --stock 601633 --market cn
    uv run -m cagent.pipeline --stock 601633 --market cn --force   # 重跑全部产物

产物在就跳过，`--force` 才重跑：中断后再跑一次等于接着跑。多家公司交给外层脚本循环调用即可。
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from cagent.facts import DETAILS, extract_financials
from cagent.llm import LLM
from cagent.report import build_report
from cagent.scraper.lakehouse_index import atomic_write

logger = logging.getLogger(__name__)


def run_company(agent: LLM, market: str, stock: str, root: Path, out: Path, force: bool = False) -> None:
    """抽齐这家公司的年报事实，再装配报告。"""
    derived = root / market / stock / "derived"
    reports = sorted(derived.glob("*.md"))
    if not reports:
        raise SystemExit(f"{derived} 没有年报 md，先跑 cagent.ocr.cli")

    for md_path in reports:
        artifact = root / market / stock / "knowledge" / md_path.stem / "financials.json"
        if artifact.exists() and not force:
            logger.info(f"复用: {artifact.name}")
            continue
        logger.info(f"抽取 {md_path.stem} …")
        facts = extract_financials(agent, md_path, root, market, stock, details=DETAILS)
        artifact.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(artifact, (json.dumps(facts, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
        logger.info(f"写出 {artifact}（未通过 {facts['unresolved']}）")

    build_report(agent, stock, market, root, out / market / stock, force=force)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S", stream=sys.stderr)
    parser = argparse.ArgumentParser(description="pipeline：一家公司跑到底")
    parser.add_argument("--stock", default="601633")
    parser.add_argument("--market", default="cn", choices=["cn", "hk"])
    parser.add_argument("--root", type=Path, default=Path(".cagent"))
    parser.add_argument("--out", type=Path, default=Path("output/wiki"))
    parser.add_argument("--force", action="store_true", help="重跑已有产物")
    args = parser.parse_args()

    agent = LLM(trace=True)
    started = time.time()
    run_company(agent, args.market, args.stock, args.root, args.out, force=args.force)
    print(f"{args.market}/{args.stock} 完成 | 墙钟 {time.time() - started:,.0f}s | {agent.ledger.summary()}")


if __name__ == "__main__":
    main()
