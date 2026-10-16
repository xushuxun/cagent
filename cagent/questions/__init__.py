"""questions：问题注册表（阅读顺序）

一个 section 一个文件（q01…q21 各自独立、都能单独执行），这里只按阅读顺序登记成一份清单：

    uv run -m cagent.questions                    # 跑全部问题
    uv run -m cagent.questions --question q19     # 只跑一问
    uv run -m cagent.questions.q19_cash_divergence  # 同一件事，单独执行

问题定义是数据不是流程：加一个问题就加一个文件、在下面登记一行，引擎不动。
"""

import argparse
import logging
import sys
from importlib import import_module
from pathlib import Path

from cagent.llm import LLM
from cagent.questions.engine import Question, answer, run
from cagent.series import load_series

logger = logging.getLogger(__name__)

# 阅读顺序即报告顺序；一个 section 一个文件，加问题就加一行
_MODULES: dict[str, str] = {
    "q1": "cagent.questions.q1_roe",
    "q2": "cagent.questions.q2_incremental_return",
    "q3": "cagent.questions.q3_sustainability",
    "q7": "cagent.questions.q7_asset_to_expense",
    "q9": "cagent.questions.q9_cash_collected",
    "q10": "cagent.questions.q10_shareholder_cash",
    "q11": "cagent.questions.q11_idle_assets",
    "q14": "cagent.questions.q14_offbook_obligations",
    "q15": "cagent.questions.q15_equity_water",
    "q17": "cagent.questions.q17_revenue_destination",
    "q18": "cagent.questions.q18_financial_vs_operating",
    "q19": "cagent.questions.q19_cash_divergence",
    "q20": "cagent.questions.q20_cash_combo",
    "q21": "cagent.questions.q21_ratio_jumps",
}


def load_questions() -> tuple[Question, ...]:
    """按阅读顺序导入各问题文件。惰性导入：单独执行某个问题时不连带导入其余问题。"""
    return tuple(import_module(name).QUESTION for name in _MODULES.values())


def __getattr__(name: str):   # PEP 562：`from cagent.questions import QUESTIONS` 时才真正导入
    if name == "QUESTIONS":
        return load_questions()
    if name == "QUESTION_BY_ID":
        return {question.id: question for question in load_questions()}
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S", stream=sys.stderr)
    parser = argparse.ArgumentParser(description="questions：按问题出结论")
    parser.add_argument("--stock", default="601633")
    parser.add_argument("--market", default="cn", choices=["cn", "hk"])
    parser.add_argument("--root", type=Path, default=Path(".cagent"))
    parser.add_argument("--out", type=Path, default=Path("output/wiki"))
    parser.add_argument("--question", default="", help="只跑某一个问题（如 q19）")
    parser.add_argument("--force", action="store_true", help="重跑（默认复用已有小节）")
    args = parser.parse_args()

    if args.question:
        run({question.id: question for question in load_questions()}[args.question], ["--stock", args.stock, "--market", args.market, "--root", str(args.root), "--out", str(args.out)] + (["--force"] if args.force else []))
        return

    agent = LLM(trace=True)
    series = load_series(args.root, args.market, args.stock)
    years = sorted(series)
    output_dir = args.out / args.market / args.stock
    for question in load_questions():
        target = output_dir / f"{question.id}.md"
        if target.exists() and not args.force:
            logger.info(f"复用: {target.name}")
            continue
        text, score, _ = answer(agent, series, years, question)
        body = f"## {question.title}\n\n> 判据：{question.judgment}\n\n{text}\n"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        logger.info(f"{question.id} {question.title}：越界 {score} 个{'（已收敛）' if score == 0 else '（未收敛）'} → {target}")
    print(agent.ledger.summary())


if __name__ == "__main__":
    main()
