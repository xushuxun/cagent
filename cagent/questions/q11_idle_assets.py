"""Q11 有没有资产占着资本不产出

判据：冗余资产在账上、在总资产里、在 ROA 的分母里，却不给分子贡献——两年以上应收占总资产是学术验证过的信号（course.md 五）

单独执行：
    uv run cagent/q11_idle_assets.py --stock 601633 --market cn
"""

from cagent.questions.engine import Question, _details_table, run
from cagent.series import asset_quality_table


def _evidence(series: dict[int, dict], years: list[int]) -> str:
    """占着资本不产出的资产：两年以上应收占总资产（固定字段）+ 占款类明细。"""
    return (
        "## 资产质量（亿元；取自年报附注）\n\n"
        + asset_quality_table(series, years)
        + "\n\n## 占款与关联往来的明细（逐年；行名照年报原文）\n\n"
        + _details_table(series, years, "related")
    )


QUESTION = Question(
    id="q11",
    title="有没有资产占着资本不产出",
    judgment="冗余资产在账上、在总资产里、在 ROA 的分母里，却不给分子贡献——两年以上应收占总资产是学术验证过的信号（course.md 五）",
    instruction=(
        "先看两年以上应收账款占总资产的比例这几年是升还是降、坏账准备是否随之增加；"
        "再看明细里有没有金额较大的非经营性占款（其他应收款、关联往来、押金保证金等），逐项说清规模与年份。"
        "只描述事实与规模，不评价好坏。"
    ),
    evidence=_evidence,
)


if __name__ == "__main__":
    run(QUESTION)
