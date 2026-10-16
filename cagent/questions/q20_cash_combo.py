"""Q20 三项现金流组合说明什么

判据：经营活动现金流能不能覆盖投资、是否靠筹资输血（course.md 九）

单独执行：
    uv run cagent/q20_cash_combo.py --stock 601633 --market cn
"""

from cagent.questions.engine import Question, run
from cagent.series import cash_reconciliation_table, cash_table


def _evidence(series: dict[int, dict], years: list[int]) -> str:
    return "## 现金流结构（占收入比；净现比、收现比为倍数）\n\n" + cash_table(series, years) + "\n\n## 经营活动现金流量调节表（亿元）\n\n" + cash_reconciliation_table(series, years)


QUESTION = Question(
    id="q20",
    title="三项现金流组合说明什么",
    judgment="经营活动现金流能不能覆盖投资、是否靠筹资输血（course.md 九）",
    instruction="逐年看经营、投资、筹资三项净额占收入的比例，以及经营+投资合计与三项合计，说明这些年扩张主要靠经营挣的钱还是靠筹资，哪几年是净流入、哪几年净流出。",
    evidence=_evidence,
)


if __name__ == "__main__":
    run(QUESTION)
