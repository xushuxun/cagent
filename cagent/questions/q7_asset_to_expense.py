"""Q7 账上的资产是怎么一步步变成费用的

判据：所有资产都是待摊费用：折旧、结转、减值、坏账、转固，逐笔变成某一年的成本费用或损失（course.md 三）

单独执行：
    uv run cagent/q7_asset_to_expense.py --stock 601633 --market cn
"""

from cagent.questions.engine import Question, _da_table, _details_table, run


def _evidence(series: dict[int, dict], years: list[int]) -> str:
    """资产怎么变成费用：折旧摊销与减值（固定字段）+ 逐年的转化明细。"""
    return (
        "## 折旧摊销与减值（亿元；取自补充资料与利润表）\n\n"
        + _da_table(series, years)
        + "\n\n## 资产端的转化事实（逐年明细；行名照年报原文）\n\n"
        + _details_table(series, years, "transfer")
    )


QUESTION = Question(
    id="q7",
    title="账上的资产是怎么一步步变成费用的",
    judgment="所有资产都是待摊费用：折旧、结转、减值、坏账、转固，逐笔变成某一年的成本费用或损失（course.md 三）",
    instruction=(
        "逐年说清资产端往费用端的转化：这一年折旧摊销了多少、计提减值多少、有多少在建工程转入固定资产、"
        "应收与存货的准备怎么变。明细行名照年报原文引用（如「转入固定资产」），金额照抄，并说明哪一年的转化规模变化最大。"
    ),
    evidence=_evidence,
)


if __name__ == "__main__":
    run(QUESTION)
