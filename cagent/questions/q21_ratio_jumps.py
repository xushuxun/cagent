"""Q21 哪些比率发生了突变

判据：等式保证异常必然留痕，纵向对比找突变（course.md 十）

单独执行：
    uv run cagent/q21_ratio_jumps.py --stock 601633 --market cn
"""

from cagent.questions.engine import Question, run
from cagent.series import ratio_delta_table, ratio_jump_table, ratio_table


def _evidence(series: dict[int, dict], years: list[int]) -> str:
    """纵向突变：比率本身与它的同比变化。"""
    return (
        "## 成本费用率与周转率\n\n"
        + ratio_table(series, years)
        + "\n\n## 比率突变的排名（程序排名，取每年变化最大的三项）\n\n"
        + ratio_jump_table(series, years)
        + "\n\n## 费用率与周转率的同比变化（率用百分点 pp，周转率用次）\n\n"
        + ratio_delta_table(series, years)
    )


QUESTION = Question(
    id="q21",
    title="哪些比率发生了突变",
    judgment="等式保证异常必然留痕，纵向对比找突变（course.md 十）",
    instruction=(
        "逐年说明「比率突变的排名」表里变化最大的三项（表已按幅度降序排好，不论幅度大小都要说到），"
        "并指出哪些项是单向一次、哪些项连续多年同向变化（后者看同比变化表判断）。只描述变化本身与幅度，不解释原因。"
    ),
    evidence=_evidence,
)


if __name__ == "__main__":
    run(QUESTION)
