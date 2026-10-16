"""Q19 净利与经营现金流为什么背离

判据：净利润与经营活动现金流长期背离，必有蹊跷（course.md 九）

单独执行：
    uv run cagent/q19_cash_divergence.py --stock 601633 --market cn
"""

from cagent.questions.engine import Question, run
from cagent.series import cash_gap_table, cash_reconciliation_table, series_table


def _evidence(series: dict[int, dict], years: list[int]) -> str:
    """净利与经营现金流：差额表（程序算好的差额）+ 多年序列 + 调节表（差额由哪些科目造成）。"""
    return (
        "## 净利与经营现金流的差额（亿元；差额与净现比由程序算好）\n\n"
        + cash_gap_table(series, years)
        + "\n\n## 多年序列（亿元）\n\n"
        + series_table(series, years)
        + "\n\n## 经营活动现金流量调节表（亿元）\n\n"
        + cash_reconciliation_table(series, years)
    )


QUESTION = Question(
    id="q19",
    title="净利与经营现金流为什么背离",
    judgment="净利润与经营活动现金流长期背离，必有蹊跷（course.md 九）",
    instruction=(
        "逐年看净利润与经营活动现金流净额是否背离：哪几年背离、方向如何、差多少。"
        "再看调节表里是哪些科目造成了这个差额（折旧摊销、减值、经营性应收应付、存货等），"
        "把造成差额的主要几项说清楚。最后给一句结论：这些年净利润里有多少是收回了现金的。"
    ),
    evidence=_evidence,
)


if __name__ == "__main__":
    run(QUESTION)
