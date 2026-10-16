"""Q2 再投进去的资本回报有没有递减

判据：重要的不是去年赚多少，而是再投进去的每一元赚多少；留存要过一元检验（course.md 一）

单独执行：
    uv run cagent/q2_incremental_return.py --stock 601633 --market cn
"""

from cagent.questions.engine import Question, run
from cagent.series import cumulative_table, series_table


def _evidence(series: dict[int, dict], years: list[int]) -> str:
    return "## 累计与滚动（亿元）\n\n" + cumulative_table(series, years) + "\n\n## 多年序列（亿元）\n\n" + series_table(series, years)


QUESTION = Question(
    id="q2",
    title="再投进去的资本回报有没有递减",
    judgment="重要的不是去年赚多少，而是再投进去的每一元赚多少；留存要过一元检验（course.md 一）",
    instruction="看滚动窗口的增量资本回报（近 N 年累计归母净利 ÷ 累计股东投入与留存）与一元检验的数值，判断这几年的增量回报是在提高还是下降、相对稳定在什么水平。",
    evidence=_evidence,
)


if __name__ == "__main__":
    run(QUESTION)
