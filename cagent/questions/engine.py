"""question：一个问题的引擎

每个问题一个文件（`q01_roe.py` … `q21_ratio_jumps.py`），都能单独执行；
这里只放共用件：`Question` 数据结构、`answer()`（一次生成 + 越界反馈重写）、`run()`（命令行入口）。

    uv run cagent/q19_cash_divergence.py            # 只跑这一问
    uv run cagent/questions.py --question q19       # 批量入口里挑一问
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
from cagent.llm import LLM
from cagent.series import load_series

logger = logging.getLogger(__name__)


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



def run(question: Question, argv: list[str] | None = None) -> Path:
    """命令行入口：跑这一问，正文写到 output/wiki/<market>/<stock>/<id>.md。"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S", stream=sys.stderr)
    parser = argparse.ArgumentParser(description=f"{question.id}：{question.title}")
    parser.add_argument("--stock", default="601633")
    parser.add_argument("--market", default="cn", choices=["cn", "hk"])
    parser.add_argument("--root", type=Path, default=Path(".cagent"))
    parser.add_argument("--out", type=Path, default=Path("output/wiki"))
    parser.add_argument("--force", action="store_true", help="重跑（默认复用已有小节）")
    args = parser.parse_args(argv)

    target = args.out / args.market / args.stock / f"{question.id}.md"
    if target.exists() and not args.force:
        logger.info(f"复用: {target}")
        return target

    agent = LLM(trace=True)
    series = load_series(args.root, args.market, args.stock)
    text, score, feedback = answer(agent, series, sorted(series), question)
    body = f"## {question.title}\n\n> 判据：{question.judgment}\n\n{text}\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    logger.info(f"{question.id} {question.title}：越界 {score} 个{'（已收敛）' if score == 0 else '（未收敛：' + feedback[:60] + '）'} → {target}")
    print(agent.ledger.summary())
    return target
