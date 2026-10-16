"""TOC 构建 cli：

    # 全量构建某公司 derived/ 下所有年报的 toc.json，已有的跳过
    uv run cagent/chunk.py --stock 601633 --market cn

    # 单文件调试
    uv run cagent/chunk.py -i .cagent/cn/601633/derived/2026-03-28_1225047452.md

幂等断点续跑：<stem>.toc.json 已存在则跳过，重跑同一命令即可续跑。
"""

import argparse
import json
import logging
import re
import sys
import textwrap
from pathlib import Path

from cagent.llm import LLM

logger = logging.getLogger(__name__)

_PAGE_RE = re.compile(r"<!-- page \d+ -->")
_TAG_RE = re.compile(r"<[^>]+>")


def _plain(text: str) -> str:
    """去掉表格标签，留下可读文本，供片段清单展示。"""
    return _TAG_RE.sub(" ", text).strip()


def report_year(agent: LLM, md_path: str | Path) -> int:
    """报告期年份：问模型这份年报属于哪个年度。md 文件名是披露日（次年披露），不是报告期。"""
    md_path = Path(md_path)
    cache = md_path.with_suffix(".year.json")
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))["year"]
    head = md_path.read_text(encoding="utf-8")[:2000]
    result = agent.chat_json(
        textwrap.dedent(f"""
            这是上市公司年报的开头，这份年报属于哪个年度（报告期年份）？

            <report>
            {head}
            </report>""").strip(),
        {"type": "object", "properties": {"year": {"type": "integer"}}, "required": ["year"]},
    )
    cache.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result["year"]

_fewshots_toc = [
    textwrap.dedent("""
        2021年年度报告

        ## 目录

        第一节 释义.....4  
        第二节 公司简介和主要财务指标.....5  
        第三节 董事长致辞.....13  
        第四节 管理层讨论与分析.....15  
        第五节 董事会报告.....50  
        第六节 监事会报告.....55  
        第七节 公司治理.....58  
        第八节 环境与社会责任.....95  
        第九节 重要事项.....113  
        第十节 股份变动及股东情况.....131  
        第十一节 优先股相关情况.....140  
        第十二节 债券相关情况.....141  
        第十三节 财务报告.....144

        <table border=1><tr><td rowspan="2">备查文件目录</td><td>载有法定代表人、主管会计工作负责人、会计机构负责人签名并盖章的财务报表原件。</td></tr><tr><td>载有会计师事务所盖章、注册会计师签名并盖章的审计报告原件。</td></tr></table>

        3 / 300
        """),
    textwrap.dedent("""
        ## 目錄

        公司資料 2  
        主要摘要 3  
        四年財務摘要 4  
        業務回顧 5  
        管理層討論及分析 7  
        董事會報告 11  
        監事會報告 33  
        董事、監事及高級管理層 35  
        企業管治報告 43  
        獨立核數師報告 61  
        合併資產負債表 66  
        合併全面虧損表 68  
        合併權益變動表 69  
        合併現金流量表 70  
        合併財務報表附註 71  
        釋義 168

        CII
        """),
]


_fewshots_toc_extract: list[dict] = [
    {
        "chapters": [
            {"title": "第一节 释义", "page": 4},
            {"title": "第二节 公司简介和主要财务指标", "page": 5},
            {"title": "第三节 董事长致辞", "page": 13},
            {"title": "第四节 管理层讨论与分析", "page": 15},
            {"title": "第五节 董事会报告", "page": 50},
            {"title": "第六节 监事会报告", "page": 55},
            {"title": "第七节 公司治理", "page": 58},
            {"title": "第八节 环境与社会责任", "page": 95},
            {"title": "第九节 重要事项", "page": 113},
            {"title": "第十节 股份变动及股东情况", "page": 131},
            {"title": "第十一节 优先股相关情况", "page": 140},
            {"title": "第十二节 债券相关情况", "page": 141},
            {"title": "第十三节 财务报告", "page": 144},
        ]
    },
    {
        "chapters": [
            {"title": "公司資料", "page": 2},
            {"title": "主要摘要", "page": 3},
            {"title": "四年財務摘要", "page": 4},
            {"title": "業務回顧", "page": 5},
            {"title": "管理層討論及分析", "page": 7},
            {"title": "董事會報告", "page": 11},
            {"title": "監事會報告", "page": 33},
            {"title": "董事、監事及高級管理層", "page": 35},
            {"title": "企業管治報告", "page": 43},
            {"title": "獨立核數師報告", "page": 61},
            {"title": "合併資產負債表", "page": 66},
            {"title": "合併全面虧損表", "page": 68},
            {"title": "合併權益變動表", "page": 69},
            {"title": "合併現金流量表", "page": 70},
            {"title": "合併財務報表附註", "page": 71},
            {"title": "釋義", "page": 168},
        ]
    },
]


def split_pages(text: str) -> list[str]:
    """按 ocr/cli.py 生成的 <!-- page N --> 标签切成页文本列表；页码 = 下标 + 1。

    产物以 <!-- page 1 --> 开头，split 首元素是首标签前的空串，[1:] 丢掉它对齐下标。
    """
    return _PAGE_RE.split(text)[1:]

def _is_page_english(agent: LLM, page_text: str) -> bool:
    prompt = textwrap.dedent(f"""
        <requirements>
        页面是否是英文内容
        </requirements>

        <page-content>
        {page_text}
        </page-content>
    """)

    response_format = {
        "type": "object",
        "properties": {"is_english": {"type": "boolean"}},
        "required": ["is_english"],
    }
    return agent.chat_json(prompt=prompt, schema=response_format)["is_english"] 


def _is_page_has_toc(agent: LLM, page_text: str) -> bool:
    prompt = textwrap.dedent(f"""
        <requirements>
        判断页面是否是年报正文的目录页
        </requirements>

        <examples>
        {"\n\n".join(f"<example>\n{e}\n</example>" for e in _fewshots_toc)}
        </examples>

        请判断以下页面内容：
        <page-content>
        {page_text}
        </page-content>
    """)

    response_format = {
        "type": "object",
        "properties": {"has_toc": {"type": "boolean"}},
        "required": ["has_toc"],
    }
    return agent.chat_json(prompt=prompt, schema=response_format)["has_toc"]


def _extract_toc_from_text(agent: LLM, toc_text: str) -> list[dict]:
    prompt = textwrap.dedent(f"""
        <requirements>
        提取目录中章节及其页码
        </requirements>

        <examples>
            {
        "\n\n".join(
            f"<example>\n<toc-content>\n{toc}\n</toc-content>\n<output>\n{json.dumps(chapters, ensure_ascii=False)}\n</output>\n</example>"
            for toc, chapters in zip(_fewshots_toc, _fewshots_toc_extract)
        )
    }
        </examples>

        请提取以下目录内容：
        <toc-content>
        {toc_text}
        </toc-content>
    """)

    response_format = {
        "type": "object",
        "properties": {
            "chapters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "page": {"type": "integer"},
                    },
                    "required": ["title", "page"],
                },
            }
        },
        "required": ["chapters"],
    }
    return agent.chat_json(prompt=prompt, schema=response_format)["chapters"]


def _locate_chapter_first_page(agent: LLM, pages: list[str], chapter: dict, lo: int, hi: int) -> int | None:
    """在页码 [lo, hi] 窗口内让 LLM 定位章节起始页，返回页码或 None。"""
    lo, hi = max(1, lo), min(len(pages), hi)
    prompt = textwrap.dedent(f"""
        <requirements>
        在 <pages> 中找出章节「{chapter["title"]}」正文开始的那一页，返回其 index。
        该章节在目录中的印刷页码是 {chapter["page"]}，其起始页的页首或页尾通常印有该页码。
        只出现在页眉、页脚、目录、引用其他章节处的不算；窗口内没有则返回 null。
        </requirements>

        <pages>
        {"\n".join(f'<page index="{i}">\n{pages[i - 1]}\n</page>' for i in range(lo, hi + 1))}
        </pages>
    """).strip()
    response_format = {
        "type": "object",
        "properties": {"index": {"type": ["integer", "null"]}},
        "required": ["index"],
    }
    index = agent.chat_json(prompt=prompt, schema=response_format)["index"]
    return index if index is not None and lo <= index <= hi else None


def _compute_page_offset(agent: LLM, pages: list[str], chapter: dict) -> int:
    """用 LLM 在目录印刷页码附近定位章节起始页，返回 offset = md 页码 - 印刷页码。

    定位窗口以印刷页码为中心，前后共 5 页，找不到扩到 9 页。
    """
    printed = chapter["page"]
    for radius in (2, 4):
        index = _locate_chapter_first_page(agent, pages, chapter, printed - radius, printed + radius)
        if index is not None:
            return index - printed
    raise ValueError(f"无法定位章节起始页：{chapter['title']}")


def _is_page_chapter_first(agent: LLM, chapter: dict, page_text: str) -> bool:
    """让 agent 判断单页是否是指定章节的起始页。"""
    prompt = textwrap.dedent(f"""
        <requirements>
        判断页面内容是否是指定章节的起始页（章节标题出现，或章节封面/扉页）
        </requirements>

        <chapter>
        {chapter["title"]}
        </chapter>

        <page-content>
        {page_text}
        </page-content>
    """).strip()
    response_format = {
        "type": "object",
        "properties": {"is_chapter_start": {"type": "boolean"}},
        "required": ["is_chapter_start"],
    }
    return agent.chat_json(prompt=prompt, schema=response_format)["is_chapter_start"]


def _align_toc(agent: LLM, pages: list[str], chapters: list[dict]) -> list[dict]:
    """从第一章算 offset，逐章用单页判断校验预测起始页；不正确时从该章重算 offset。

    返回 [{"title", "page", "md_page"}]，md_page 为对齐后的 md 起始页码。
    """
    aligned = []
    offset = None
    for ch in chapters:
        if offset is not None:
            md_page = ch["page"] + offset
            if 0 < md_page <= len(pages) and _is_page_chapter_first(agent, ch, pages[md_page - 1]):
                aligned.append({**ch, "md_page": md_page})
                continue
        try:
            offset = _compute_page_offset(agent, pages, ch)
        except ValueError as e:
            logger.warning(f"跳过无法定位的章节: {e}")
            continue
        aligned.append({**ch, "md_page": ch["page"] + offset})
    return aligned


class Toc:
    """年报目录：章节列表 + 页码范围，以及原始 md。"""

    def __init__(self, agent: LLM, md_path: str | Path, rebuild: bool = False):
        self.agent = agent

        md_path = Path(md_path)
        toc_file = md_path.with_suffix(".toc.json")
        if not toc_file.exists() or rebuild:
            self._build_toc(md_path)

        self.chapters = json.loads(toc_file.read_text(encoding="utf-8"))  # [{"title", "page", "md_page"}]
        self.text = md_path.read_text(encoding="utf-8")
        self.n_pages = len(split_pages(self.text))
        self._pick_script()   # 双语年报（港股常见：前一半英文、后一半繁体）只留中文半本

    def _text_between(self, start: int, end: int) -> str:
        begin = self.text.find(f"<!-- page {start} -->")
        finish = self.text.find(f"<!-- page {end + 1} -->")
        return self.text[begin : finish if finish != -1 else len(self.text)] if begin != -1 else ""

    def _cjk_ratio(self, ch: dict) -> float:
        """章节正文的汉字占比：英文半本 ≈ 0，繁体中文半本接近 1（表里夹英文行名也不影响）。"""
        sample = self._text_between(ch["md_page"], ch["md_page"] + 2)[:6000]
        cjk = len(re.findall(r"[\u4e00-\u9fff]", sample))
        latin = len(re.findall(r"[A-Za-z]", sample))
        return cjk / (cjk + latin) if cjk + latin else 0.0

    def _pick_script(self) -> None:
        """认一下语料形态，只用于诊断与日志，**不做裁剪**。

        港股年报常见两种双语形态，处理方式完全不同：
            分半本      前一半英文、后一半中文——目录（toc.json）本来只覆盖中文半本，管线读的就是中文；
            页内对照    财务报表页里中文行名与英文行名并排——数字同一份，按中文行名标注即可，不必拆。
        唯一的坑是"页内对照"页的汉字占比很低（实测 0.14–0.19），若按占比裁章节会把报表整章误删，
        所以这里只判形态、只记日志，绝不据此增删章节。
        """
        ratios = [self._cjk_ratio(ch) for ch in self.chapters]
        chinese = [ratio > 0.25 for ratio in ratios]
        if all(chinese):
            self.script = "zh"
        elif not any(chinese):
            self.script = "en"
        else:
            self.script = "mixed"
        logger.debug(f"语料形态 {self.script}：汉字占比 {[round(r, 2) for r in ratios]}")

    def range(self, ch: dict) -> tuple[int, int]:
        """章节的 md 页码范围 (start, end)。"""
        i = self.chapters.index(ch)
        start = ch["md_page"]
        end = self.chapters[i + 1]["md_page"] - 1 if i + 1 < len(self.chapters) else self.n_pages
        return start, end

    def chapter_text(self, i: int) -> str:
        """原始 md 中第 i 章页码范围对应的原文（含页标记）。"""
        lo, hi = self.range(self.chapters[i])
        start = self.text.index(f"<!-- page {lo} -->")
        end = self.text.find(f"<!-- page {hi + 1} -->")
        return self.text[start : end if end != -1 else len(self.text)]

    def chapter_text_chunk(self, i: int, budget: int = 32768) -> list[str]:
        """按字符预算切文本
        切口优先落在 md 标题（行首 #）处，避免截断段落；标题间距超预算时退回硬切。"""
        text = self.chapter_text(i)
        heading_positions = [match.start() for match in re.finditer(r"(?m)^#", text)]
        chunks, chunk_start, previous_heading = [], 0, 0
        for position in heading_positions + [len(text)]:
            while position - chunk_start > budget:
                cut_position = previous_heading if previous_heading > chunk_start else chunk_start + budget
                chunks.append(text[chunk_start:cut_position])
                chunk_start = cut_position
            previous_heading = position
        chunks.append(text[chunk_start:])
        return chunks

    def _build_toc(self, md_path: Path) -> None:
        """跑 TOC 管线，把对齐结果写到 md 同目录的 <stem>.toc.json。"""
        annual = md_path.read_text(encoding="utf-8")
        pages = split_pages(annual)

        toc_text = ""
        for p in pages:
            has_toc = _is_page_has_toc(self.agent, p)
            if _is_page_english(self.agent, p):
                continue
            if has_toc is True:
                toc_text += p + "\n\n"
            elif has_toc is False and toc_text:
                # 已经收集到目录，遇到第一个非目录页说明目录结束
                break
        if not toc_text:
            raise ValueError(f"未找到目录页: {md_path}")

        chapters = _extract_toc_from_text(self.agent, toc_text)
        aligned = _align_toc(self.agent, pages, chapters)

        payload = [{k: c[k] for k in ("title", "page", "md_page")} for c in aligned]
        out = md_path.with_suffix(".toc.json")
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        logger.info(f"输出 TOC: {out} ({len(payload)} 章)")

    def pick_chapters(self, chapters: list[dict], task: str) -> list[int]:
        """给精确的章节标题列表和阅读目的，让模型选出要精读的章节序号。

        不做标题关键字匹配——选章是理解任务，交给模型，每家公司每年选一次。
        """
        listing = "\n".join(f'<chapter index="{index}">\n{chapter["title"]}\n</chapter>' for index, chapter in enumerate(chapters))
        prompt = textwrap.dedent(f"""
            <requirements>
            {task}
            从列表里选，返回章节序号。
            </requirements>

            <chapters>
            {listing}
            </chapters>
        """).strip()
        schema = {
            "type": "object",
            "properties": {"indexes": {"type": "array", "items": {"type": "integer"}}},
            "required": ["indexes"],
        }
        for attempt in range(2):
            indexes = self.agent.chat_json(prompt, schema)["indexes"]
            picked = [index for index in indexes if 0 <= index < len(chapters)]
            if picked:
                return picked
            logger.warning(f"章节选择第 {attempt + 1} 次没有有效序号，重试")
            prompt = f"{prompt}\n\n<retry>上一次返回的序号不在 0 到 {len(chapters) - 1} 之间，请重新给。</retry>"
        raise ValueError("模型没有返回有效章节序号")

    def pick_chunks(self, chunks: list[str], task: str, limit: int = 6) -> list[int]:
        """从切好的片段里挑相关的几个：给模型看片段编号与首行，避免为一项事实通读整章。

        片段切口落在 md 标题处，首行基本就是小节名或附注名，够模型判断相关性。
        """
        labels = []
        for index, chunk in enumerate(chunks):
            first = next((line.strip() for line in chunk.splitlines() if line.strip() and not line.startswith("<!--")), "")
            labels.append(f'<chunk index="{index}">{_plain(first)[:60]}</chunk>')
        prompt = textwrap.dedent(f"""
            <requirements>
            {task}
            从清单里挑出真正相关的片段编号，最多 {limit} 个；都不相关就返回空数组。
            </requirements>

            <chunks>
            {"".join(labels)}
            </chunks>""").strip()
        indexes = self.agent.chat_json(
            prompt,
            {"type": "object", "properties": {"indexes": {"type": "array", "items": {"type": "integer"}}}, "required": ["indexes"]},
        )["indexes"]
        return [index for index in indexes if 0 <= index < len(chunks)][:limit]

    def gen_source(self, pick_chapters: list[int]) -> dict:
        return {
            "chapters": [
                {
                    "chapter": self.chapters[i]["title"],
                    "md_page": [self.chapters[i]["md_page"], self.chapters[i + 1]["md_page"] - 1],
                }
                for i in pick_chapters
            ]
        }


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )

    parser = argparse.ArgumentParser(description="构建年报章节目录 toc.json")
    parser.add_argument("--stock", help="股票代码，如 601633、09863")
    parser.add_argument("--market", choices=["cn", "hk"], help="目标市场")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1] / ".cagent"))
    parser.add_argument("--force", action="store_true", help="重建已存在的 toc.json")
    parser.add_argument("-i", "--input", help="单文件调试模式，指定 md 文件路径")
    args = parser.parse_args()

    agent = LLM()

    if args.input:
        toc = Toc(agent, args.input)
        print(toc.chapters)
        sys.exit(0)

    md_dir = Path(args.root) / str(args.market) / str(args.stock) / "derived"
    mds = sorted(md_dir.glob("*.md"))
    if not mds:
        logger.error(f"{md_dir} 没有解析产物，先跑 ocr/cli.py")
        sys.exit(1)
    for md in mds:
        if md.with_suffix(".toc.json").exists() and not args.force:
            logger.info(f"跳过（已存在）: {md.name}")
            continue
        logger.info(f"构建 TOC: {md.name}")
        toc = Toc(agent, md, args.force)
        print(toc.chapters)
