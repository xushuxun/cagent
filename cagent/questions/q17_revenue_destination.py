"""Q17 每 100 元收入被谁分走

判据：成本费用收入比就是股东和其他利益相关方的讨价还价能力（course.md 八）

单独执行：
    uv run cagent/q17_revenue_destination.py --stock 601633 --market cn
"""

from cagent.questions.engine import Question, run
from cagent.series import income_level_table, income_use_table


def _evidence(series: dict[int, dict], years: list[int]) -> str:
    """收入去向：每 100 元收入被谁分走。"""
    return "## 每 100 元收入去向（元）\n\n" + income_use_table(series, years) + "\n\n## 利润表科目（亿元）\n\n" + income_level_table(series, years)


QUESTION = Question(
    id="q17",
    title="每 100 元收入被谁分走",
    judgment="成本费用收入比就是股东和其他利益相关方的讨价还价能力（course.md 八）",
    instruction=(
        "看每 100 元收入的去向：成本、各项费用、税金、所得税、净利润各占多少，"
        "再看这几年股东拿到的份额是在扩大还是缩小，变化幅度多大。"
    ),
    evidence=_evidence,
)


if __name__ == "__main__":
    run(QUESTION)
