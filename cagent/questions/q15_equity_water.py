"""Q15 权益里有多少是没变现的公允价值

判据：权益端的水分：公允价值计量把尚未变现的盈利直接计入股东权益（course.md 六）

单独执行：
    uv run cagent/q15_equity_water.py --stock 601633 --market cn
"""

from cagent.questions.engine import Question, run
from cagent.series import oci_table, series_table


def _evidence(series: dict[int, dict], years: list[int]) -> str:
    return "## 权益里的其他综合收益（亿元）\n\n" + oci_table(series, years) + "\n\n## 多年序列（归母净资产部分，亿元）\n\n" + series_table(series, years)


QUESTION = Question(
    id="q15",
    title="权益里有多少是没变现的公允价值",
    judgment="权益端的水分：公允价值计量把尚未变现的盈利直接计入股东权益（course.md 六）",
    instruction="看其他综合收益每年的金额与累计金额、累计额占归母权益的比例，说明这几年累计下来是多少、方向如何。",
    evidence=_evidence,
)


if __name__ == "__main__":
    run(QUESTION)
