import json
import logging
import re
import textwrap
from pathlib import Path
from pprint import pprint

from cagent.agent import Agent

logger = logging.getLogger(__name__)

agent = Agent()

PAGE_RE = re.compile(r"<!-- page \d+ -->")

fewshots_toc = [
"""
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
""",
"""
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
""",
]
fewshots_toc_extract = [
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
    return PAGE_RE.split(text)[1:]


def is_page_has_toc(agent: Agent, page_text: str) -> bool:
    prompt = textwrap.dedent(f"""
        <requirements>
        判断页面是否是年报正文的目录页
        </requirements>

        <examples>
        {"\n\n".join(f"<example>\n{e}\n</example>" for e in fewshots_toc)}
        </examples>

        请判断以下页面内容：
        <page-content>
        {page_text}
        </page-content>

    """).strip()

    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "has_toc",
            "schema": {
                "type": "object",
                "properties": {"has_toc": {"type": "boolean"}},
                "required": ["has_toc"],
            },
        },
    }
    response = json.loads(
        agent.chat(
            messages=[{"role": "user", "content": prompt}],
            response_format=response_format,
        )
    )
    return response["has_toc"]


def extract_toc(agent: Agent, toc_text: str) -> list[dict]:
    """用 LLM 从目录页文本提取一级章节，返回 [{"title", "page"}]，page 为目录印刷页码。"""

    examples = "\n\n".join(
        f"<example>\n<toc-content>\n{toc}\n</toc-content>\n<output>\n{json.dumps(chapters, ensure_ascii=False)}\n</output>\n</example>"
        for toc, chapters in zip(fewshots_toc, fewshots_toc_extract)
    )

    prompt = textwrap.dedent(f"""
        <requirements>
        提取目录中章节及其页码
        </requirements>

        <examples>
        {examples}
        </examples>

        请提取以下目录内容：
        <toc-content>
        {toc_text}
        </toc-content>
    """).strip()

    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "toc",
            "schema": {
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
            },
        },
    }
    response = json.loads(
        agent.chat(
            messages=[{"role": "user", "content": prompt}],
            response_format=response_format,
        )
    )
    return response["chapters"]


def locate_chapter_page(agent: Agent, pages: list[str], chapter: dict, lo: int, hi: int) -> int | None:
    """在页码 [lo, hi] 窗口内让 LLM 定位章节起始页，返回页码或 None。"""
    lo, hi = max(1, lo), min(len(pages), hi)
    window = "\n".join(f"<page index=\"{i}\">\n{pages[i - 1]}\n</page>" for i in range(lo, hi + 1))
    prompt = textwrap.dedent(f"""
        <requirements>
        在 <pages> 中找出章节「{chapter["title"]}」正文开始的那一页，返回其 index。
        该章节在目录中的印刷页码是 {chapter["page"]}，其起始页的页首或页尾通常印有该页码。
        只出现在页眉、页脚、目录、引用其他章节处的不算；窗口内没有则返回 null。
        </requirements>

        <pages>
        {window}
        </pages>
    """).strip()
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "chapter_page",
            "schema": {
                "type": "object",
                "properties": {"index": {"type": ["integer", "null"]}},
                "required": ["index"],
            },
        },
    }
    index = json.loads(
        agent.chat(
            messages=[{"role": "user", "content": prompt}],
            response_format=response_format,
        )
    )["index"]
    return index if index is not None and lo <= index <= hi else None


def compute_offset(agent: Agent, pages: list[str], chapter: dict) -> int:
    """用 LLM 在目录印刷页码附近定位章节起始页，返回 offset = md 页码 - 印刷页码。

    定位窗口以印刷页码为中心，前后共 5 页，找不到扩到 9 页。
    """
    printed = chapter["page"]
    for radius in (2, 4):
        index = locate_chapter_page(agent, pages, chapter, printed - radius, printed + radius)
        if index is not None:
            return index - printed
    raise ValueError(f"无法定位章节起始页：{chapter['title']}")


def is_chapter_start(agent: Agent, chapter: dict, page_text: str) -> bool:
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
        "type": "json_schema",
        "json_schema": {
            "name": "is_chapter_start",
            "schema": {
                "type": "object",
                "properties": {"is_chapter_start": {"type": "boolean"}},
                "required": ["is_chapter_start"],
            },
        },
    }
    response = json.loads(
        agent.chat(
            messages=[{"role": "user", "content": prompt}],
            response_format=response_format,
        )
    )
    return response["is_chapter_start"]


def align_toc(agent: Agent, pages: list[str], chapters: list[dict]) -> list[dict]:
    """从第一章算 offset，逐章用单页判断校验预测起始页；不正确时从该章重算 offset。

    返回 [{"title", "page", "md_page"}]，md_page 为对齐后的 md 起始页码。
    """
    aligned = []
    offset = None
    for ch in chapters:
        if offset is not None:
            md_page = ch["page"] + offset
            if 0 < md_page <= len(pages) and is_chapter_start(agent, ch, pages[md_page - 1]):
                aligned.append({**ch, "md_page": md_page})
                continue
        offset = compute_offset(agent, pages, ch)
        aligned.append({**ch, "md_page": ch["page"] + offset})
    return aligned


if __name__ == "__main__":
    annual = (Path(__file__).parent.parent / ".cagent/hk/09863/derived/2023-04-17_2023041701384_c.md").read_text()
    pages = split_pages(annual)

    toc_text = ""
    for p in pages[:20]:
        has_toc = is_page_has_toc(agent, p)
        if has_toc is True:
            toc_text += p + "\n\n"
        elif has_toc is False and toc_text:
            # 已经收集到目录，遇到第一个非目录页说明目录结束
            break

    chapters = extract_toc(agent, toc_text)
    pprint(chapters)

    aligned = align_toc(agent, pages, chapters)
    pprint(aligned)

