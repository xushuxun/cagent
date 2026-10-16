"""Q3 利润与现金的水平能不能持续

判据：六角度里的 S：当前盈利水平与现金水平能否持续（course.md 一）

单独执行：
    uv run cagent/q3_sustainability.py --stock 601633 --market cn
"""

from cagent.questions.engine import Question, run
from cagent.series import growth_table, series_table


def _evidence(series: dict[int, dict], years: list[int]) -> str:
    return "## 多年序列（亿元）\n\n" + series_table(series, years) + "\n\n## 同比变化与自由现金流\n\n" + growth_table(series, years)


QUESTION = Question(
    id="q3",
    title="利润与现金的水平能不能持续",
    judgment="六角度里的 S：当前盈利水平与现金水平能否持续（course.md 一）",
    instruction="看这些年收入、归母净利、经营现金流、自由现金流的水平与波动：哪些年下滑、幅度多大、之后是否恢复。只描述已发生的波动。",
    evidence=_evidence,
)


if __name__ == "__main__":
    run(QUESTION)
