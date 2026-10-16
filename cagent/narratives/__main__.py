"""批量入口：跑全部叙述节（uv run -m cagent.narratives）"""

import argparse
import sys
from pathlib import Path

from cagent.narratives.business import main as business_main
from cagent.narratives.decisions import main as decisions_main


def main() -> None:
    parser = argparse.ArgumentParser(description="叙述节：生意模式 + 历年经营决策")
    parser.add_argument("--stock", default="601633")
    parser.add_argument("--market", default="cn", choices=["cn", "hk"])
    parser.add_argument("--root", type=Path, default=Path(".cagent"))
    parser.add_argument("--out", type=Path, default=Path("output/wiki"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    for module_main in (business_main, decisions_main):
        sys.argv = ["narratives", "--stock", args.stock, "--market", args.market, "--root", str(args.root), "--out", str(args.out)] + (["--force"] if args.force else [])
        module_main()


if __name__ == "__main__":
    main()
