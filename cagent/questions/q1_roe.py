"""Q1 这门生意为资本赚了多少、还在往上走吗

判据：最终要回到 ROE：这门生意用股东投入赚回了多少（course.md 一）

单独执行：
    uv run cagent/q1_roe.py --stock 601633 --market cn
"""

from cagent.questions.engine import Question, run
from cagent.series import ratio_table, series_table


def _evidence(series: dict[int, dict], years: list[int]) -> str:
    return "## 多年序列（亿元）\n\n" + series_table(series, years) + "\n\n## 成本费用率与周转率\n\n" + ratio_table(series, years)


QUESTION = Question(
    id="q1",
    title="这门生意为资本赚了多少、还在往上走吗",
    judgment="最终要回到 ROE：这门生意用股东投入赚回了多少（course.md 一）",
    instruction="逐年看 ROE 的水平与方向、归母净利与归母净资产的增速差（决定 ROE 是升是降），并指出这几年 ROE 的高点与低点。只描述水平与方向。",
    evidence=_evidence,
)


if __name__ == "__main__":
    run(QUESTION)
