"""Q14 有没有没入表的义务

判据：已确认的负债问题不大，风险在没入账的义务：环保、未决诉讼、期权与衍生品、对外担保（course.md 六）

单独执行：
    uv run cagent/q14_offbook_obligations.py --stock 601633 --market cn
"""

from cagent.questions.engine import Question, _details_table, run
from cagent.series import asset_quality_table


def _evidence(series: dict[int, dict], years: list[int]) -> str:
    """没入表的义务：担保、诉讼、承诺、衍生品、受限资产（逐年明细）。"""
    return (
        "## 受限资金与资产质量（亿元）\n\n"
        + asset_quality_table(series, years)
        + "\n\n## 表外义务明细（逐年；行名照年报原文）\n\n"
        + _details_table(series, years, "obligation")
    )


QUESTION = Question(
    id="q14",
    title="有没有没入表的义务",
    judgment="已确认的负债问题不大，风险在没入账的义务：环保、未决诉讼、期权与衍生品、对外担保（course.md 六）",
    instruction=(
        "逐年列出披露的表外义务：对外担保余额（含对子公司）、未决诉讼标的、已签约但未拨备的承诺、"
        "衍生金融工具的期末公允价值、使用受限的货币资金。哪一年没有披露就写「该年未抽到披露」，不要推测。"
    ),
    evidence=_evidence,
)


if __name__ == "__main__":
    run(QUESTION)
