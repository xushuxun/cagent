"""Q18 利润里有多少靠天吃饭

判据：经营性损益靠人吃饭、可持续；财务性损益靠天吃饭——突然多一块不必高兴，亏一块也不必难过（course.md 八）

单独执行：
    uv run cagent/q18_financial_vs_operating.py --stock 601633 --market cn
"""

from cagent.questions.engine import Question, run
from cagent.series import income_quality_table, income_use_table


def _evidence(series: dict[int, dict], years: list[int]) -> str:
    return "## 经营性 vs 财务性损益（亿元）\n\n" + income_quality_table(series, years) + "\n\n## 每 100 元收入去向（元）\n\n" + income_use_table(series, years)


QUESTION = Question(
    id="q18",
    title="利润里有多少靠天吃饭",
    judgment="经营性损益靠人吃饭、可持续；财务性损益靠天吃饭——突然多一块不必高兴，亏一块也不必难过（course.md 八）",
    instruction="逐年看财务性损益（投资收益、公允价值变动、资产处置收益）合计占利润总额的比例，以及经营性损益的规模与占比，指出哪一年财务性占比最高。",
    evidence=_evidence,
)


if __name__ == "__main__":
    run(QUESTION)
