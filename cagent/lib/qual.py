"""定性提取：按目录选章，逐章定长摘要（带页码索引）+ 管理层叙事。

decode 是瓶颈——摘要定长输出（每段 ≤8 条、每条一句话），输出量与章节数成正比
而非与原文成正比；结构化（证据 JSON）只在合并环节做一次。财务数字不走这里
（见 accounting.py 的程序直解通道）。无缓存：每次全量重算。
"""

import logging
from pathlib import Path

import dspy

from cagent.llm import parse_json, tag
from cagent.pageindex.reader import PageIndexReader

logger = logging.getLogger(__name__)


class PickQualChapters(dspy.Signature):
    """从章节目录选出定性证据提取需要的章节：业务与经营讨论（MD&A：业务模式、
量价、竞争格局、现金流变动解释）、董事长致辞/管理层讨论（对行业与自身的判断、
计划与承诺）、公司治理与重要事项（审计意见、关联交易、资金占用、诉讼、股权质押、
分红回购）、主要会计数据/财务摘要。宁可多选不要漏选；封面、释义、股东名册、
债券、备查文件等无关章节不选。chapters 输出 JSON 整数数组。"""

    catalog: str = dspy.InputField(desc="公告信息 + 章节目录（序号、标题、页码范围）")
    chapters: str = dspy.OutputField(desc="JSON 整数数组，如 [2, 4, 7]")


class SummarizeChapter(dspy.Signature):
    """你是价值投资者的助手。输入是年报某章节的原文片段
（OCR markdown，<!-- page N --> 是物理页码标记）。为该片段写简短摘要，
供后续撰写价值投资报告使用。只记与片段相关的以下要点（有则写，无则不写）：
- 诚信红线：审计意见、会计差错/重述、监管立案、资金占用、重大诉讼、股权质押；
- 生意与竞争：谁在付钱、为何付钱、客户/供应商集中度、量价、成本大头、
  管理层对竞争优势的表述（照抄原文短句）；
- 现金流成因：管理层对经营/投资/筹资现金流变动原因的解释、资本开支投向；
- 管理层言行：对行业与自身处境的判断、明确承诺/计划（"将""计划""目标"）、
  实际行动（并购/扩产/分红/回购/募资变更）。
要求：最多 8 条，每条一句话（≤50 字），末尾标页码 (p.N)（用 <!-- page N --> 的
数字）；数字保留原始单位；没有上述内容就输出"（无）"；输出简体中文 bullet 列表。"""

    passage: str = dspy.InputField(desc="公告信息 + 章节标题与页码范围 + 原文片段")
    bullets: str = dspy.OutputField(desc="bullet 列表，每条带 (p.N)")


class NarrativeFromSummaries(dspy.Signature):
    """你是价值投资者的助手。输入是一份年报相关章节的摘要（每条带页码）。
提取该年报对应报告期的"管理层叙事"——管理层怎么想的、打算怎么做、实际做了什么。
每条内容必须来自摘要，保留页码标注 (p.N)；摘要没有依据的填 "未披露"，禁止编造。
年份只填本年报覆盖的报告期（通常是公告日期的上一年）；promises 必须是明确的
意图表述（"将""计划""目标"），不是已经发生的事。"""

    summaries: str = dspy.InputField(desc="公告信息 + 章节摘要")
    narrative: str = dspy.OutputField(
        desc='JSON {"entries": [{"year", "management_said", "promises": [], '
             '"tone", "capital_plan", "actions": []}]}')


class QualExtractor(dspy.Module):
    """单份公告：选章 → 逐段定长摘要 → 从摘要提取管理层叙事。"""

    def __init__(self):
        super().__init__()
        self.pick = dspy.Predict(PickQualChapters)
        self.summarize = dspy.Predict(SummarizeChapter)
        self.narrate = dspy.Predict(NarrativeFromSummaries)

    def forward(self, lm, reader: PageIndexReader, meta: dict, idx: int,
                total: int) -> dict | None:
        units = reader.reading_units(12000) or []
        if not units:
            logger.warning("定性提取 %s 无章节（无目录），跳过", meta["file"])
            return None
        catalog = "\n".join(f"{k}. {u.title}（p.{u.start}-p.{u.end}）"
                            for k, u in enumerate(units, 1))
        with tag(f"qual:pick:{meta['file']}"):
            pred = self.pick(
                catalog=f"公告：{meta['date']} {meta['title']}\n\n章节目录：{catalog}",
                lm=lm, config={"max_tokens": 1000})
        picks = parse_json(pred.chapters)
        valid = sorted({k for k in (picks if isinstance(picks, list) else [])
                        if isinstance(k, int) and 1 <= k <= len(units)})
        if not valid:
            logger.warning("定性提取 %s 选章为空，跳过", meta["file"])
            return None

        head = f"第 {idx}/{total} 份公告：{meta['date']} {meta['title']}"
        chapters: list[dict] = []
        for k in valid:
            u = units[k - 1]
            bullets: list[str] = []
            for j, part in enumerate(u.parts, 1):
                span = f"（本章第 {j}/{len(u.parts)} 段）" if len(u.parts) > 1 else ""
                with tag(f"qual:{meta['file']}#{k}.{j}"):
                    pred = self.summarize(
                        passage=f"{head}\n章节：{u.title}（p.{u.start}-p.{u.end}）{span}\n\n{part}",
                        lm=lm, config={"max_tokens": 800})
                text = (pred.bullets or "").strip()
                if text and text != "（无）":
                    bullets.append(text)
            if bullets:
                chapters.append({"title": u.title, "start": u.start, "end": u.end,
                                 "summary": "\n".join(bullets)})
        if not chapters:
            return None

        summaries_text = "\n\n".join(
            f"### {c['title']}（p.{c['start']}-p.{c['end']}）\n{c['summary']}" for c in chapters)
        with tag(f"narrative:{meta['file']}"):
            pred = self.narrate(
                summaries=f"{head}\n\n章节摘要：\n\n{summaries_text}",
                lm=lm, config={"max_tokens": 2500})
        narrative = parse_json(pred.narrative)

        return {"chapters": chapters,
                "narrative": narrative if isinstance(narrative, dict) else None}


def build_qual(lm, reader, derived: Path, filings: list[dict]) -> list[dict]:
    """逐份提取章节摘要与叙事。依赖 md 旁的 .pageindex.json 构建产物（缺失的份跳过）。

    返回 [{"filing", "file", "summaries", "narrative"}]（按时间顺序）；
    summaries 为 Markdown 文本（### 章节标题 + bullet 摘要，带页码）。
    """
    extractor = QualExtractor()
    out: list[dict] = []
    for i, f in enumerate(filings, 1):
        logger.info("定性提取 %d/%d: %s %s", i, len(filings), f["date"], f["title"])
        data = extractor(lm, PageIndexReader(derived / f["file"], reader), f, i, len(filings))
        if data is None:
            logger.warning("定性提取失败 %d/%d: %s", i, len(filings), f["file"])
            continue
        summaries = "\n\n".join(
            f"### {c['title']}（p.{c['start']}-p.{c['end']}）\n{c['summary']}"
            for c in data.get("chapters") or [])
        out.append({"filing": f"{f['date']} {f['title']}", "file": f["file"],
                    "summaries": summaries, "narrative": data.get("narrative")})
    return out
