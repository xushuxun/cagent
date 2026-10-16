"""Q9 报的利润收回了多少现金

判据：漏斗一：报了利润收不回现金，利润表好看、现金流量表难看（course.md 四）

单独执行：
    uv run cagent/q9_cash_collected.py --stock 601633 --market cn
"""

from cagent.questions.engine import Question, run
from cagent.series import cash_gap_table, cash_table


def _evidence(series: dict[int, dict], years: list[int]) -> str:
    return "## 现金流结构（占收入比）\n\n" + cash_table(series, years) + "\n\n## 净利与经营现金流的差额（亿元）\n\n" + cash_gap_table(series, years)


QUESTION = Question(
    id="q9",
    title="报的利润收回了多少现金",
    judgment="漏斗一：报了利润收不回现金，利润表好看、现金流量表难看（course.md 四）",
    instruction="逐年看净现比与收现比、经营现金流占收入比，指出报出的利润里收回现金的比例是多少、哪一年最低。",
    evidence=_evidence,
)


if __name__ == "__main__":
    run(QUESTION)
