"""sections：叙述节（与问题层同一套接口与纪律）

问题层的证据是程序算好的表；叙述节的证据是年报原文（公司介绍、管理层讨论），
但两者输出同一份东西：**可被程序校验的正文**。

- 带财务证据的叙述节：正文里的数字只许来自证据表（`checks.grounded_check`），越界就带反馈重写；
- 引用年报原文的地方标注页码，页码必须落在这一节读过的片段里；
- 一节可以有几个面（如"管理层判断"与"当年决策"），逐年写的节按年份组织。

用法：
    from cagent.narratives.engine import SECTIONS, write_section
    doc = write_section(agent, BUSINESS, stock, market, root, evidence_text)
"""

import logging
import re
import textwrap
from dataclasses import dataclass
from pathlib import Path

from cagent.checks import grounded_check
from cagent.chunk import Toc, report_year
from cagent.facts import _fit_chunks
from cagent.llm import LLM

logger = logging.getLogger(__name__)

_PAGE_MARK = re.compile(r"<!-- page (\d+) -->")
_PAGE_CITE = re.compile(r"p\.\s?(\d+)")


@dataclass(frozen=True)
class Section:
    """一节叙述：读哪些章节、写哪几个面、要不要带财务证据。"""

    id: str
    title: str
    pick_task: str                      # 章节检索任务
    parts: tuple[tuple[str, str], ...]  # (小标题, 只写这个面的要求)
    examples: tuple[str, ...] = ()
    per_report: bool = False            # 逐年写（读每一份年报）
    with_evidence: bool = False         # 带上财务证据，正文数字受校验
    max_tokens: int = 1600


def _fit_section_chunks(agent: LLM, chunks: list[str]) -> list[str]:
    return _fit_chunks(agent, chunks)


def _cited_pages(text: str) -> set[int]:
    return {int(match) for match in _PAGE_CITE.findall(text)}


def _write_part(agent: LLM, section: Section, title: str, instruction: str, chunks: list[str], evidence_text: str = "") -> str:
    examples = "\n".join(f"<example>{item}</example>" for item in section.examples)
    prompt = textwrap.dedent(f"""
        <requirement>
        {instruction}

        - 只写原文有依据的内容，禁止编造原文不存在的细节；去掉管理层吹嘘话术，语言简洁凝炼
        - 引用年报里的具体说法时标注页码，写成「（见年报 p.N）」
        - 用简体中文，markdown 格式
        </requirement>
        """ + (f"\n<examples>\n{examples}\n</examples>" if examples else "")).strip()
    if evidence_text and section.with_evidence:
        prompt = f"{prompt}\n\n<evidence>\n{evidence_text}\n</evidence>"
    if evidence_text and section.with_evidence:
        prompt += "\n\n需要量化时只引用 <evidence> 里的数字，数字照抄；不得自行计算或外推。"

    text = agent.distill(prompt, chunks)

    if evidence_text and section.with_evidence:
        # 叙述节的数字有两个合法来源：读过的年报原文，与给它的财务证据；
        # 页码必须落在读过的片段里——出问题就把具体位置反馈回去重写一次
        allowed_text = "\n".join(chunks) + "\n" + evidence_text
        problems = list(grounded_check(text, allowed_text).missing)
        available_pages = {int(match) for match in _PAGE_MARK.findall("\n".join(chunks))}
        bad_pages = sorted(page for page in _cited_pages(text) if available_pages and page not in available_pages)
        if bad_pages:
            problems.append("页码不在读过的片段里：" + "、".join(f"p.{page}" for page in bad_pages[:3]))
        if problems:
            logger.warning(f"{section.id} {title}：{len(problems)} 处越界，反馈重写")
            text = agent.distill(f"{prompt}\n\n<violations>\n上一稿有这些问题：{'；'.join(problems)}\n只许用读过的原文与给定证据里的数重写一遍。\n</violations>", chunks)
            remaining = list(grounded_check(text, allowed_text).missing)
            if remaining:
                logger.warning(f"{section.id} {title}：重写后仍有 {len(remaining)} 处越界：{'、'.join(remaining[:5])}")
    return text.strip()


def write_section(agent: LLM, section: Section, stock: str, market: str, root: Path, evidence_text: str = "") -> str:
    """写一节叙述：检索章节 → 逐面蒸馏 → （带证据时）校验正文数字。"""
    derived = root / market / stock / "derived"
    mds = sorted(derived.glob("*.md"))
    if not mds:
        raise SystemExit(f"{derived} 没有年报 md，先跑 ocr/cli.py")
    reports = {report_year(agent, path): path for path in mds}
    ordered = sorted(reports.items())
    picked_reports = ordered[-5:] if section.per_report else [ordered[-1]]

    parts = [f"# {section.title}\n"]
    for year, md_path in picked_reports:
        toc = Toc(agent, md_path)
        picked = toc.pick_chapters(toc.chapters, section.pick_task)
        chunks = _fit_section_chunks(agent, [chunk for index in picked for chunk in toc.chapter_text_chunk(index)])
        logger.info(f"{section.id} {year}: 章节 {[toc.chapters[i]['title'] for i in picked]} / 片段 {len(chunks)}")
        if section.per_report:
            parts.append(f"## {year}\n")
        for title, instruction in section.parts:
            body = _write_part(agent, section, title, instruction, chunks, evidence_text)
            if not body:
                raise ValueError(f"{section.id} {year} {title} 生成异常")
            parts.append(f"**{title}**\n\n{body}\n" if section.per_report else f"{body}\n")
    return "\n".join(parts).strip() + "\n"
