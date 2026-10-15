"""questions：问题层

一个问题 = 一条课程判据 + 一组证据 + 一次生成 + 一个可判定的验收。
问题定义是数据不是流程：加一个问题就加一条定义，引擎不动。

验收由程序判：正文里的每个数字都要能在证据表里找到（checks.grounded_check）——模型自算的差额、
占比、外推都算越界。越界就把越界的数反馈回去重写，只在变好时采纳；收敛不了如实报分数。

证据一律取自序列层的表构造函数，算术只在那里发生一次。

用法：
    from cagent.questions import QUESTIONS, answer
    text, score, feedback = answer(agent, series, years, Q19)

    uv run cagent/questions.py --stock 601633 --market cn            # 回答全部问题
    uv run cagent/questions.py --stock 601633 --market cn --question q19
"""

import argparse
import json
import logging
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from cagent.checks import grounded_check
from cagent.finance_series import (
    asset_quality_table,
    cash_gap_table,
    cash_reconciliation_table,
    cash_table,
    cumulative_table,
    growth_table,
    income_level_table,
    income_quality_table,
    income_use_table,
    load_series,
    oci_table,
    ratio_delta_table,
    ratio_jump_table,
    ratio_table,
    series_table,
)
from cagent.llm import LLM

logger = logging.getLogger(__name__)

# 产品纪律：不进证据的数字不说、不做预测与结论——与 prd 的边界一致
_DISCIPLINE = """<discipline>
- 只用 <evidence> 里的数，数字照抄；不许自己算差额、占比、倍数、合计，也不许四舍五入成整数
- 说不清的地方直接写"证据表未显示"，不要推测原因，不下投资结论，不预测未来
- 每个结论后面用（来源：表名）注明来自哪张表
- 不要复述 <evidence> 的内容（表格、口径说明都不抄），只写这一问的结论
- 先给结论再给依据：第一句直接回答这一问，后面才是支撑它的数字
</discipline>"""

# 答案用 JSON 包一层：模型没法"续写"证据表，输出形状也可判
_ANSWER_SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}


def _strip_echo(text: str, evidence_text: str) -> str:
    """模型偶尔会把证据抄回来：去掉抄回的整块与整行，只留结论（程序侧兜底，不靠提示词自觉）。"""
    text = re.sub(r"<evidence>.*?</evidence>", "", text, flags=re.DOTALL)
    echoed = {line.strip() for line in evidence_text.splitlines() if line.strip()}
    return "\n".join(line for line in text.splitlines() if line.strip() and line.strip() not in echoed).strip()


@dataclass(frozen=True)
class Question:
    """一个问题：判据、证据、只写这一问的要求。"""

    id: str
    title: str
    judgment: str                                    # 课程给的判据，写在文档里给读者看
    instruction: str                                 # 只写这一问，不越界到别的问题
    evidence: Callable[[dict[int, dict], list[int]], str]
    max_tokens: int = 1280                           # 每问的输出上限：逐年走一遍的问题需要更多空间


def _q19_evidence(series: dict[int, dict], years: list[int]) -> str:
    """净利与经营现金流：差额表（程序算好的差额）+ 多年序列 + 调节表（差额由哪些科目造成）。"""
    return (
        "## 净利与经营现金流的差额（亿元；差额与净现比由程序算好）\n\n"
        + cash_gap_table(series, years)
        + "\n\n## 多年序列（亿元）\n\n"
        + series_table(series, years)
        + "\n\n## 经营活动现金流量调节表（亿元）\n\n"
        + cash_reconciliation_table(series, years)
    )


Q19 = Question(
    id="q19",
    title="净利与经营现金流为什么背离",
    judgment="净利润与经营活动现金流长期背离，必有蹊跷（course.md 九）",
    instruction=(
        "逐年看净利润与经营活动现金流净额是否背离：哪几年背离、方向如何、差多少。"
        "再看调节表里是哪些科目造成了这个差额（折旧摊销、减值、经营性应收应付、存货等），"
        "把造成差额的主要几项说清楚。最后给一句结论：这些年净利润里有多少是收回了现金的。"
    ),
    evidence=_q19_evidence,
)

def _da_table(series: dict[int, dict], years: list[int]) -> str:
    """折旧摊销与减值：固定字段（补充资料 + 利润表），只有这一层是多年可比的。"""
    keys = (("da_fixed", "固定资产折旧"), ("da_rou", "使用权资产折旧"), ("da_intangible", "无形资产摊销"), ("da_lt_deferred", "长期待摊费用摊销"), ("asset_impairment", "资产减值损失"), ("credit_impairment", "信用减值损失"))
    body = ["| 年份 | " + " | ".join(label for _, label in keys) + " | 合计 |", "| --- |" + " --- |" * (len(keys) + 1)]
    for year in years:
        roles = series[year]["roles"]
        values = [roles.get(role) for role, _ in keys]
        total = sum(value for value in values[:4] if value is not None)
        cells = [f"{value / 1e8:,.1f}亿" if value is not None else "—" for value in values]
        body.append(f"| {year} | " + " | ".join(cells) + f" | {total / 1e8:,.1f}亿 |")
    return "\n".join(body)


def _details_table(series: dict[int, dict], years: list[int], kind: str) -> str:
    """按需明细的逐年清单：行名照原文 + 金额 + 页码。不做汇总——汇总要跨年同名，而明细本来就不保证同名。"""
    lines = []
    for year in years:
        rows = [row for row in (series[year].get("details") or []) if row.get("kind") == kind]
        if not rows:
            lines.append(f"### {year} 年\n\n（该年未抽到这一类明细）")
            continue
        body = ["| 披露名（原文） | 本期 | 上期 | 页码 |", "| --- | --- | --- | --- |"]
        for row in rows:
            value = row.get("value")
            prev = row.get("prev")
            body.append(
                f"| {row.get('label')} | {value / 1e8:,.1f}亿 | {prev / 1e8:,.1f}亿 | p.{row.get('page')} |"
                if value is not None and prev is not None
                else f"| {row.get('label')} | {value / 1e8:,.1f}亿 | — | p.{row.get('page')} |"
                if value is not None
                else f"| {row.get('label')} | — | — | p.{row.get('page')} |"
            )
        lines.append(f"### {year} 年\n\n" + "\n".join(body))
    return "\n\n".join(lines)


def _q7_evidence(series: dict[int, dict], years: list[int]) -> str:
    """资产怎么变成费用：折旧摊销与减值（固定字段）+ 逐年的转化明细。"""
    return (
        "## 折旧摊销与减值（亿元；取自补充资料与利润表）\n\n"
        + _da_table(series, years)
        + "\n\n## 资产端的转化事实（逐年明细；行名照年报原文）\n\n"
        + _details_table(series, years, "transfer")
    )


def _q11_evidence(series: dict[int, dict], years: list[int]) -> str:
    """占着资本不产出的资产：两年以上应收占总资产（固定字段）+ 占款类明细。"""
    return (
        "## 资产质量（亿元；取自年报附注）\n\n"
        + asset_quality_table(series, years)
        + "\n\n## 占款与关联往来的明细（逐年；行名照年报原文）\n\n"
        + _details_table(series, years, "related")
    )


def _q14_evidence(series: dict[int, dict], years: list[int]) -> str:
    """没入表的义务：担保、诉讼、承诺、衍生品、受限资产（逐年明细）。"""
    return (
        "## 受限资金与资产质量（亿元）\n\n"
        + asset_quality_table(series, years)
        + "\n\n## 表外义务明细（逐年；行名照年报原文）\n\n"
        + _details_table(series, years, "obligation")
    )


def _q10_evidence(series: dict[int, dict], years: list[int]) -> str:
    """漏斗二：收回了现金，有多少到了股东手里。累计口径是课程的判据所在。"""
    return (
        "## 逐年分红与留存（亿元）\n\n"
        + growth_table(series, years)
        + "\n\n## 多年序列（亿元）\n\n"
        + series_table(series, years)
        + "\n\n## 累计与滚动（亿元）\n\n"
        + cumulative_table(series, years)
    )


def _q21_evidence(series: dict[int, dict], years: list[int]) -> str:
    """纵向突变：比率本身与它的同比变化。"""
    return (
        "## 成本费用率与周转率\n\n"
        + ratio_table(series, years)
        + "\n\n## 比率突变的排名（程序排名，取每年变化最大的三项）\n\n"
        + ratio_jump_table(series, years)
        + "\n\n## 费用率与周转率的同比变化（率用百分点 pp，周转率用次）\n\n"
        + ratio_delta_table(series, years)
    )


def _q17_evidence(series: dict[int, dict], years: list[int]) -> str:
    """收入去向：每 100 元收入被谁分走。"""
    return "## 每 100 元收入去向（元）\n\n" + income_use_table(series, years) + "\n\n## 利润表科目（亿元）\n\n" + income_level_table(series, years)


Q10 = Question(
    id="q10",
    title="收回的钱有多少到了股东手里",
    judgment="收回了现金，到不了股东手里——被占用、被再投资到低回报项目上（course.md 四）",
    instruction=(
        "先看逐年：分红占归母净利的比例是多少、留存下来多少。"
        "再看累计口径：这些年累计留存了多少钱、累计股东投入多少、增量资本回报与一元检验是什么水平。"
        "把「报的利润→收回的现金→到股东手里」这条链条上每一段的规模说清楚，指出断在哪一段。"
    ),
    evidence=_q10_evidence,
)

Q21 = Question(
    id="q21",
    title="哪些比率发生了突变",
    judgment="等式保证异常必然留痕，纵向对比找突变（course.md 十）",
    instruction=(
        "逐年说明「比率突变的排名」表里变化最大的三项（表已按幅度降序排好，不论幅度大小都要说到），"
        "并指出哪些项是单向一次、哪些项连续多年同向变化（后者看同比变化表判断）。只描述变化本身与幅度，不解释原因。"
    ),
    evidence=_q21_evidence,
)

Q17 = Question(
    id="q17",
    title="每 100 元收入被谁分走",
    judgment="成本费用收入比就是股东和其他利益相关方的讨价还价能力（course.md 八）",
    instruction=(
        "看每 100 元收入的去向：成本、各项费用、税金、所得税、净利润各占多少，"
        "再看这几年股东拿到的份额是在扩大还是缩小，变化幅度多大。"
    ),
    evidence=_q17_evidence,
)

Q7 = Question(
    id="q7",
    title="账上的资产是怎么一步步变成费用的",
    judgment="所有资产都是待摊费用：折旧、结转、减值、坏账、转固，逐笔变成某一年的成本费用或损失（course.md 三）",
    instruction=(
        "逐年说清资产端往费用端的转化：这一年折旧摊销了多少、计提减值多少、有多少在建工程转入固定资产、"
        "应收与存货的准备怎么变。明细行名照年报原文引用（如「转入固定资产」），金额照抄，并说明哪一年的转化规模变化最大。"
    ),
    evidence=_q7_evidence,
)

Q11 = Question(
    id="q11",
    title="有没有资产占着资本不产出",
    judgment="冗余资产在账上、在总资产里、在 ROA 的分母里，却不给分子贡献——两年以上应收占总资产是学术验证过的信号（course.md 五）",
    instruction=(
        "先看两年以上应收账款占总资产的比例这几年是升还是降、坏账准备是否随之增加；"
        "再看明细里有没有金额较大的非经营性占款（其他应收款、关联往来、押金保证金等），逐项说清规模与年份。"
        "只描述事实与规模，不评价好坏。"
    ),
    evidence=_q11_evidence,
)

Q14 = Question(
    id="q14",
    title="有没有没入表的义务",
    judgment="已确认的负债问题不大，风险在没入账的义务：环保、未决诉讼、期权与衍生品、对外担保（course.md 六）",
    instruction=(
        "逐年列出披露的表外义务：对外担保余额（含对子公司）、未决诉讼标的、已签约但未拨备的承诺、"
        "衍生金融工具的期末公允价值、使用受限的货币资金。哪一年没有披露就写「该年未抽到披露」，不要推测。"
    ),
    evidence=_q14_evidence,
)

def _q1_evidence(series: dict[int, dict], years: list[int]) -> str:
    return "## 多年序列（亿元）\n\n" + series_table(series, years) + "\n\n## 成本费用率与周转率\n\n" + ratio_table(series, years)


def _q2_evidence(series: dict[int, dict], years: list[int]) -> str:
    return "## 累计与滚动（亿元）\n\n" + cumulative_table(series, years) + "\n\n## 多年序列（亿元）\n\n" + series_table(series, years)


def _q3_evidence(series: dict[int, dict], years: list[int]) -> str:
    return "## 多年序列（亿元）\n\n" + series_table(series, years) + "\n\n## 同比变化与自由现金流\n\n" + growth_table(series, years)


def _q9_evidence(series: dict[int, dict], years: list[int]) -> str:
    return "## 现金流结构（占收入比）\n\n" + cash_table(series, years) + "\n\n## 净利与经营现金流的差额（亿元）\n\n" + cash_gap_table(series, years)


def _q15_evidence(series: dict[int, dict], years: list[int]) -> str:
    return "## 权益里的其他综合收益（亿元）\n\n" + oci_table(series, years) + "\n\n## 多年序列（归母净资产部分，亿元）\n\n" + series_table(series, years)


def _q18_evidence(series: dict[int, dict], years: list[int]) -> str:
    return "## 经营性 vs 财务性损益（亿元）\n\n" + income_quality_table(series, years) + "\n\n## 每 100 元收入去向（元）\n\n" + income_use_table(series, years)


def _q20_evidence(series: dict[int, dict], years: list[int]) -> str:
    return "## 现金流结构（占收入比；净现比、收现比为倍数）\n\n" + cash_table(series, years) + "\n\n## 经营活动现金流量调节表（亿元）\n\n" + cash_reconciliation_table(series, years)


Q1 = Question(
    id="q1",
    title="这门生意为资本赚了多少、还在往上走吗",
    judgment="最终要回到 ROE：这门生意用股东投入赚回了多少（course.md 一）",
    instruction="逐年看 ROE 的水平与方向、归母净利与归母净资产的增速差（决定 ROE 是升是降），并指出这几年 ROE 的高点与低点。只描述水平与方向。",
    evidence=_q1_evidence,
)

Q2 = Question(
    id="q2",
    title="再投进去的资本回报有没有递减",
    judgment="重要的不是去年赚多少，而是再投进去的每一元赚多少；留存要过一元检验（course.md 一）",
    instruction="看滚动窗口的增量资本回报（近 N 年累计归母净利 ÷ 累计股东投入与留存）与一元检验的数值，判断这几年的增量回报是在提高还是下降、相对稳定在什么水平。",
    evidence=_q2_evidence,
)

Q3 = Question(
    id="q3",
    title="利润与现金的水平能不能持续",
    judgment="六角度里的 S：当前盈利水平与现金水平能否持续（course.md 一）",
    instruction="看这些年收入、归母净利、经营现金流、自由现金流的水平与波动：哪些年下滑、幅度多大、之后是否恢复。只描述已发生的波动。",
    evidence=_q3_evidence,
)

Q9 = Question(
    id="q9",
    title="报的利润收回了多少现金",
    judgment="漏斗一：报了利润收不回现金，利润表好看、现金流量表难看（course.md 四）",
    instruction="逐年看净现比与收现比、经营现金流占收入比，指出报出的利润里收回现金的比例是多少、哪一年最低。",
    evidence=_q9_evidence,
)

Q15 = Question(
    id="q15",
    title="权益里有多少是没变现的公允价值",
    judgment="权益端的水分：公允价值计量把尚未变现的盈利直接计入股东权益（course.md 六）",
    instruction="看其他综合收益每年的金额与累计金额、累计额占归母权益的比例，说明这几年累计下来是多少、方向如何。",
    evidence=_q15_evidence,
)

Q18 = Question(
    id="q18",
    title="利润里有多少靠天吃饭",
    judgment="经营性损益靠人吃饭、可持续；财务性损益靠天吃饭——突然多一块不必高兴，亏一块也不必难过（course.md 八）",
    instruction="逐年看财务性损益（投资收益、公允价值变动、资产处置收益）合计占利润总额的比例，以及经营性损益的规模与占比，指出哪一年财务性占比最高。",
    evidence=_q18_evidence,
)

Q20 = Question(
    id="q20",
    title="三项现金流组合说明什么",
    judgment="经营活动现金流能不能覆盖投资、是否靠筹资输血（course.md 九）",
    instruction="逐年看经营、投资、筹资三项净额占收入的比例，以及经营+投资合计与三项合计，说明这些年扩张主要靠经营挣的钱还是靠筹资，哪几年是净流入、哪几年净流出。",
    evidence=_q20_evidence,
)

QUESTIONS: tuple[Question, ...] = (Q1, Q2, Q3, Q7, Q9, Q10, Q11, Q14, Q15, Q17, Q18, Q19, Q20, Q21)
QUESTION_BY_ID = {question.id: question for question in QUESTIONS}


def answer(agent: LLM, series: dict[int, dict], years: list[int], question: Question, *, attempts: int = 3) -> tuple[str, int, str]:
    """回答一个问题：一次生成 + 越界反馈重写。返回 (正文, 越界数, 最后一轮反馈)。"""
    evidence_text = question.evidence(series, years)
    # 任务写在证据之前：模型看到以表格结尾的提示词会去续写表格，任务在前才是回答
    task = agent.task(
        f"{_DISCIPLINE}\n\n<task>只回答这一问：{question.title}\n{question.instruction}</task>\n\n<evidence>\n{evidence_text}\n</evidence>"
    )

    def tail(_previous: str | None, feedback: str) -> str:
        if not feedback:
            return "<task>直接输出结论正文。</task>"
        return f"<violations>\n上一稿里这些数字不在证据表里：{feedback}\n只许用证据表里的数重写一遍，不要复述证据。\n</violations>"

    def check(text: str) -> tuple[int, str]:
        result = grounded_check(text, evidence_text)
        return len(result.missing), "、".join(result.missing)

    def parse(raw: str, _previous: str | None) -> str:
        return _strip_echo(json.loads(raw)["answer"], evidence_text)

    text, score, feedback = task.converge(tail, parse=parse, check=check, schema=_ANSWER_SCHEMA, max_tokens=question.max_tokens, attempts=attempts)
    return text or "", score, feedback


def gen_questions(agent: LLM, stock: str, market: str, root: Path, output_dir: Path, only: str = "") -> dict[str, str]:
    """把选中的问题各写成一节，落成 markdown 文件。"""
    series = load_series(root, market, stock)
    years = sorted(series)
    picked = [QUESTION_BY_ID[only]] if only else list(QUESTIONS)
    written: dict[str, str] = {}
    for question in picked:
        text, score, feedback = answer(agent, series, years, question)
        body = f"## {question.title}\n\n> 判据：{question.judgment}\n\n{text}\n"
        target = output_dir / f"{question.id}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        written[question.id] = body
        logger.info(f"{question.id} {question.title}：越界 {score} 个{'（已收敛）' if score == 0 else '（未收敛：' + feedback[:60] + '）'} → {target}")
    return written


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S", stream=sys.stderr)
    parser = argparse.ArgumentParser(description="questions：按问题出结论")
    parser.add_argument("--stock", default="601633")
    parser.add_argument("--market", default="cn", choices=["cn", "hk"])
    parser.add_argument("--root", type=Path, default=Path(".cagent"))
    parser.add_argument("--out", type=Path, default=Path("output/wiki"))
    parser.add_argument("--question", default="", help="只跑某一个问题（如 q19）")
    args = parser.parse_args()
    agent = LLM(trace=True)
    output_dir = args.out / args.market / args.stock
    written = gen_questions(agent, args.stock, args.market, args.root, output_dir, args.question)
    print(f"写出 {len(written)} 节 → {output_dir}")
    print(agent.ledger.summary())


if __name__ == "__main__":
    main()
