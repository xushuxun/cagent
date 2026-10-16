"""Q10 收回的钱有多少到了股东手里

判据：收回了现金，到不了股东手里——被占用、被再投资到低回报项目上（course.md 四）

单独执行：
    uv run cagent/q10_shareholder_cash.py --stock 601633 --market cn
"""

from cagent.questions.engine import Question, run
from cagent.series import cumulative_table, growth_table, series_table


def _evidence(series: dict[int, dict], years: list[int]) -> str:
    """漏斗二：收回了现金，有多少到了股东手里。累计口径是课程的判据所在。"""
    return (
        "## 逐年分红与留存（亿元）\n\n"
        + growth_table(series, years)
        + "\n\n## 多年序列（亿元）\n\n"
        + series_table(series, years)
        + "\n\n## 累计与滚动（亿元）\n\n"
        + cumulative_table(series, years)
    )


QUESTION = Question(
    id="q10",
    title="收回的钱有多少到了股东手里",
    judgment="收回了现金，到不了股东手里——被占用、被再投资到低回报项目上（course.md 四）",
    instruction=(
        "先看逐年：分红占归母净利的比例是多少、留存下来多少。"
        "再看累计口径：这些年累计留存了多少钱、累计股东投入多少、增量资本回报与一元检验是什么水平。"
        "把「报的利润→收回的现金→到股东手里」这条链条上每一段的规模说清楚，指出断在哪一段。"
    ),
    evidence=_evidence,
)


if __name__ == "__main__":
    run(QUESTION)
