"""页码索引（pageindex）构建：dspy 流水线（定位 → 提取 → 复核 → 对齐 → 摘要）。

用 dspy.configure 的全局 LM（cagent.llm.connect 已配置）。产物是文档的构建产物：
`.pageindex.json` 落盘 md 旁（缺失重建，--force 无条件重建）；读取侧在
同包 `reader.py`。
"""

import hashlib
import json
import logging
import re
from pathlib import Path

import dspy
from pydantic import BaseModel

from cagent.lib.lakehouse import LakehouseReader
from cagent.llm import DETERMINISTIC, tag
from cagent.pageindex.reader import PAGEINDEX_SUFFIX, pageindex_is_current

log = logging.getLogger("cagent.pageindex")

FRONT_BATCH = 6      # 定位阶段每批阅读的页数
PAGE_HEAD_LINES = 3  # 对齐阶段页索引每页取前几个非空行
PAGE_HEAD_CHARS = 60
SUMMARY_CHARS = 1500  # 摘要阶段每个条目取首页前 N 字符
SUMMARY_BATCH = 20000  # 摘要阶段每批条目的内容总字符上限


def _atomic_write_json(path: Path, data: dict) -> None:
    """先写临时文件再改名，进程中断不留下半截 .pageindex.json。"""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    tmp.replace(path)


class PageIndexEntry(BaseModel):
    """目录条目：标题、层级（从 1 起）、页码（提取阶段为印刷页码，对齐后为物理页码）。"""

    title: str
    level: int = 1
    page: int | None = None


class FindPageIndex(dspy.Signature):
    """从年报开头页面找印制的目录页。页码一律用 <!-- page N --> 的物理页码 N。"""

    pages: str = dspy.InputField(desc="一批页面原文")
    status: str = dspy.OutputField(
        desc="found（找到目录）/ continue（本批没有，继续看下一批）/ passed（已越过开头，无目录）")
    toc_pages: list[int] = dspy.OutputField(desc="status=found 时目录页的物理页码，否则空数组")


class ExtractPageIndex(dspy.Signature):
    """从目录页提取目录条目，保持原顺序；忽略封面/免责声明。"""

    toc_text: str = dspy.InputField(desc="目录页原文")
    entries: list[PageIndexEntry] = dspy.OutputField(
        desc="目录条目（标题去掉页码和点线；page 为目录标注的印刷页码，无页码给 null）")


class VerifyPageIndex(dspy.Signature):
    """对照目录页原文复核初步提取的目录条目：补遗漏、改错标题/层级/页码，保持原顺序。"""

    toc_text: str = dspy.InputField(desc="目录页原文")
    draft: list[PageIndexEntry] = dspy.InputField(desc="初步提取的条目")
    entries: list[PageIndexEntry] = dspy.OutputField(desc="复核后的完整条目")


class AlignPageIndex(dspy.Signature):
    """把目录条目（印刷页码）对齐到正文物理页码（<!-- page N -->）。
印刷与物理页码通常有固定偏移，以页首出现的章节标题为准。"""

    entries: list[PageIndexEntry] = dspy.InputField(desc="目录条目（含印刷页码）")
    page_index: str = dspy.InputField(desc='页索引，每页开头几行，格式 "N: 页首内容"')
    pages: list[int | None] = dspy.OutputField(desc="与 entries 一一对应的物理页码；拿不准的条目给 null")


class SummarizePageIndex(dspy.Signature):
    """为每个目录条目写一句话摘要（≤40 字，说明该章涵盖什么）。"""

    blocks: str = dspy.InputField(desc="按序号给出各条目标题及首页开头内容")
    summaries: list[str] = dspy.OutputField(desc="与 blocks 中的序号一一对应")


# one-shot 示教（设计原则：用例子教格式与口径，不堆砌说教式指令）

_FIND_DEMO = dspy.Example(
    pages="第 1 批（p.1-p.3，之前批次未找到目录）：\n\n"
          "<!-- page 1 -->\n某公司 2025 年年度报告\n\n"
          "<!-- page 2 -->\n目录\n第一节 释义....4\n第二节 公司简介....5\n\n"
          "<!-- page 3 -->\n第一节 释义\n在本报告中，除非文义另有所指……",
    status="found", toc_pages=[2],
).with_inputs("pages")

_EXTRACT_DEMO = dspy.Example(
    toc_text="<!-- page 2 -->\n目录\n第一节 释义....4\n第二节 管理层讨论与分析....15\n"
             "  经营情况讨论....16\n  财务回顾....30\n备查文件目录",
    entries=[{"title": "第一节 释义", "level": 1, "page": 4},
             {"title": "第二节 管理层讨论与分析", "level": 1, "page": 15},
             {"title": "经营情况讨论", "level": 2, "page": 16},
             {"title": "财务回顾", "level": 2, "page": 30},
             {"title": "备查文件目录", "level": 1, "page": None}],
).with_inputs("toc_text")

_VERIFY_DEMO = dspy.Example(
    toc_text="<!-- page 2 -->\n目录\n第一节 释义....4\n第二节 财务报告....136\n备查文件目录",
    draft=[{"title": "第一节 释义", "level": 1, "page": 4},
           {"title": "第二节 财务报告", "level": 1, "page": 136}],
    entries=[{"title": "第一节 释义", "level": 1, "page": 4},
             {"title": "第二节 财务报告", "level": 1, "page": 136},
             {"title": "备查文件目录", "level": 1, "page": None}],
).with_inputs("toc_text", "draft")

_ALIGN_DEMO = dspy.Example(
    entries=[{"title": "第一节 释义", "level": 1, "page": 4},
             {"title": "第二节 财务报告", "level": 1, "page": 136}],
    page_index="1: 某公司 2025 年年度报告 / 封面\n2: 目录\n3: 重要提示\n"
               "6: 第一节 释义 / 在本报告中……\n140: 财务报表及审计报告 / 审计报告",
    pages=[6, 140],
).with_inputs("entries", "page_index")

_SUMMARY_DEMO = dspy.Example(
    blocks="[0] 第一节 释义\n在本报告中，除非文义另有所指，下列词语具有如下含义……\n\n"
           "[1] 董事长致辞\n各位股东：2025 年公司营收创新高，新能源销量翻倍……",
    summaries=["定义报告使用的术语和缩写", "回顾年度经营业绩并展望战略方向"],
).with_inputs("blocks")


class PageIndexBuilder(dspy.Module):
    """单份 markdown 公告的页码索引构建流水线：定位 → 提取 → 复核 → 对齐 → 摘要。

    用 dspy.configure 的全局 LM（cagent.llm.connect 已配置）。
    产物是文档的构建产物：.pageindex.json 落盘 md 旁（与原文同目录），缺失重建
    （--force 无条件重建）。
    """

    def __init__(self, reader: LakehouseReader | None = None):
        super().__init__()
        self._reader = reader or LakehouseReader()
        self.find_pageindex = dspy.Predict(FindPageIndex)
        self.extract_pageindex = dspy.Predict(ExtractPageIndex)
        self.verify_pageindex = dspy.Predict(VerifyPageIndex)
        self.align_pageindex = dspy.Predict(AlignPageIndex)
        self.summarize_pageindex = dspy.Predict(SummarizePageIndex)
        self.find_pageindex.demos = [_FIND_DEMO]
        self.extract_pageindex.demos = [_EXTRACT_DEMO]
        self.verify_pageindex.demos = [_VERIFY_DEMO]
        self.align_pageindex.demos = [_ALIGN_DEMO]
        self.summarize_pageindex.demos = [_SUMMARY_DEMO]

    def build(self, md: Path, force: bool = False) -> Path:
        """为 md 构建 .pageindex.json（已存在且未 force 时直接复用），返回索引文件路径。"""
        out = md.with_suffix(PAGEINDEX_SUFFIX)
        pages = self._reader.pages_of(md.read_text(encoding="utf-8"))
        if not pages:
            raise SystemExit(f"{md} 中没有 <!-- page N --> 页码标记")
        if out.exists() and not force and pageindex_is_current(md, self._reader):
            print(f"已存在 {out.name}（--force 重建）")
            return out
        if out.exists():
            print(f"[pageindex] {out.name} 缺失/损坏/页数不一致，重建", flush=True)
        print(f"[pageindex] {md.name}: {max(pages)} 页", flush=True)
        tree = self(pages)
        _atomic_write_json(out, {
            "doc": md.name, "pages": max(pages),
            "md_sha256": hashlib.sha256(md.read_bytes()).hexdigest(),
            "structure": tree,
        })
        print(f"[pageindex] 已写出 {out.name}（{len(tree)} 个顶层节点）", flush=True)
        return out

    def forward(self, pages: dict[int, str]) -> list[dict]:
        """页文本 → 页码索引树（workflow 本体，纯内存操作）。"""
        toc_pages = self._find(pages)
        if not toc_pages:
            raise SystemExit("未找到目录页（模型判断本文档没有目录）")
        print(f"[pageindex] 目录页: p.{toc_pages[0]}-p.{toc_pages[-1]}", flush=True)

        entries = self._extract(pages, toc_pages)
        if not entries:
            raise SystemExit("目录条目提取失败")
        print(f"[pageindex] 条目 {len(entries)} 条，对齐物理页码中", flush=True)

        self._align(pages, entries)
        self._summarize(pages, entries)
        return _build_tree(entries, max(pages))

    def _find(self, pages: dict[int, str]) -> list[int]:
        """逐批阅读开头，返回目录页的物理页码列表；未找到返回空列表。"""
        ordered = sorted(pages)
        for i in range(0, len(ordered), FRONT_BATCH):
            batch = ordered[i:i + FRONT_BATCH]
            print(f"[pageindex] 定位目录：阅读 p.{batch[0]}-p.{batch[-1]}", flush=True)
            with tag("pageindex:find"):
                r = self.find_pageindex(
                    pages=f"第 {i // FRONT_BATCH + 1} 批"
                          f"（p.{batch[0]}-p.{batch[-1]}，之前批次未找到目录）：\n\n"
                          + self._reader.render_pages(pages, batch[0], batch[-1]),
                    config=DETERMINISTIC | {"max_tokens": 1000})
            if r.status == "continue":
                continue
            if r.status == "found":
                toc = sorted({n for n in r.toc_pages if n in pages})
                if toc:
                    return toc
            return []  # passed，或 found 但页码无效
        return []

    def _extract(self, pages: dict[int, str], toc_pages: list[int]) -> list[dict]:
        """从目录页提取条目，再对照原文复核一遍。返回 [{title, level, page(印刷)}]。"""
        toc_text = self._reader.render_pages(pages, toc_pages[0], toc_pages[-1])
        with tag("pageindex:extract"):
            draft = self.extract_pageindex(
                toc_text=toc_text, config=DETERMINISTIC | {"max_tokens": 4000}).entries
        print(f"[pageindex] 初步提取 {len(draft)} 条，复核中", flush=True)
        with tag("pageindex:verify"):
            final = self.verify_pageindex(toc_text=toc_text, draft=draft,
                                          config=DETERMINISTIC | {"max_tokens": 4000}).entries
        entries = []
        for e in final or draft:
            if not e.title.strip():
                continue
            entries.append({"title": e.title.strip(),
                            "level": max(e.level, 1), "page": e.page})
        return entries

    def _align(self, pages: dict[int, str], entries: list[dict]) -> None:
        """LLM 对齐，原地写入每个条目的物理页码 page。

        对齐失败（null）时分两类：原本无印刷页码的条目正文无对应章节（如备查
        文件目录），直接丢弃；有印刷页码的沿用前一条。
        """
        last = max(pages)
        page_index = "\n".join(f"{n}: {_page_head(pages[n])}" for n in sorted(pages))
        with tag("pageindex:align"):
            aligned = self.align_pageindex(
                entries=[PageIndexEntry(**e) for e in entries],
                page_index=page_index,
                config=DETERMINISTIC | {"max_tokens": 4000}).pages
        ordered = sorted(pages)
        prev = None
        kept = []
        for i, e in enumerate(entries):
            n = aligned[i] if i < len(aligned) else None
            if n is None and e["page"] is None:
                print(f"[pageindex] 条目「{e['title']}」无印刷页码且正文定位不到，丢弃", flush=True)
                continue
            if n is None:
                n = prev if prev is not None else ordered[0]
            else:
                target = min(max(n, 1), last)
                n = min(ordered, key=lambda p: (abs(p - target), p))  # 落到存在的页
            e["page"] = n
            prev = n
            kept.append(e)
        entries[:] = kept

    def _summarize(self, pages: dict[int, str], entries: list[dict]) -> None:
        """按各条目首页内容写一句话摘要，原地写入 summary。内容多时自适应分批。"""
        batch, idxs, size = [], [], 0
        batches = []
        for i, e in enumerate(entries):
            block = f"[{i}] {e['title']}\n{pages.get(e['page'], '')[:SUMMARY_CHARS]}"
            if batch and size + len(block) > SUMMARY_BATCH:
                batches.append((batch, idxs))
                batch, idxs, size = [], [], 0
            batch.append(block)
            idxs.append(i)
            size += len(block)
        if batch:
            batches.append((batch, idxs))
        for b, (blocks, idxs) in enumerate(batches, 1):
            print(f"[pageindex] 摘要 {b}/{len(batches)}（{len(idxs)} 条）", flush=True)
            with tag("pageindex:summary"):
                out = self.summarize_pageindex(blocks="\n\n".join(blocks),
                                               config=DETERMINISTIC | {"max_tokens": 4000}).summaries
            for j, i in enumerate(idxs):
                if j < len(out):
                    entries[i]["summary"] = out[j].strip()


# ---------------------------------------------------------------------------
# 模块级辅助函数与批量编排
# ---------------------------------------------------------------------------

def _build_tree(entries: list[dict], last_page: int) -> list[dict]:
    """按层级组装索引树；end_index = 下一节点 start_index - 1（父节点覆盖子树）。"""
    entries.sort(key=lambda e: e["page"])
    levels = sorted({e["level"] for e in entries})
    depth = {lv: i + 1 for i, lv in enumerate(levels)}
    root: list[dict] = []
    stack: list[tuple[int, dict]] = []
    flat: list[dict] = []
    for n, e in enumerate(entries):
        node = {"node_id": f"{n:04d}", "title": e["title"],
                "start_index": e["page"], "end_index": None,
                "summary": e.get("summary", "")}
        d = depth[e["level"]]
        while stack and stack[-1][0] >= d:
            stack.pop()
        if stack:
            stack[-1][1].setdefault("sub_nodes", []).append(node)
        else:
            root.append(node)
        stack.append((d, node))
        flat.append(node)
    for i, node in enumerate(flat):
        if "sub_nodes" not in node:
            node["end_index"] = (flat[i + 1]["start_index"] - 1
                                 if i + 1 < len(flat) else last_page)
    for node in reversed(flat):  # 反向前序：子树先于父节点处理
        if "sub_nodes" in node:
            node["end_index"] = node["sub_nodes"][-1]["end_index"]
        node["end_index"] = max(node["end_index"], node["start_index"])
    return root


def _page_head(text: str) -> str:
    """取一页的前几个非空行作为页索引（去掉图片引用等无信息行）。"""
    lines = []
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("![") or ln == "[Non-Text]":
            continue
        lines.append(re.sub(r"\s+", " ", ln)[:PAGE_HEAD_CHARS])
        if len(lines) >= PAGE_HEAD_LINES:
            break
    return " / ".join(lines)


def pageindex_company(market: str, code: str, lakehouse: Path,
                      force: bool = False) -> int:
    """为一家公司 derived/ 下所有公告确保 .pageindex.json，返回失败份数。"""
    reader = LakehouseReader(root=lakehouse)
    data_dir = reader.company_dir(market, code)
    derived = data_dir / "derived"
    if not derived.is_dir():
        return 0
    builder = PageIndexBuilder(reader)
    failed = 0
    for f in reader.list_filings(data_dir):
        md = derived / f["file"]
        if pageindex_is_current(md, reader) and not force:
            continue
        try:
            builder.build(md, force=force)
        except SystemExit:
            log.info("  %s %s 无目录（通知信函等），跳过", code, f["file"])
        except Exception as exc:
            log.error("  [FAIL] %s %s（%s）", code, f["file"], exc)
            failed += 1
    return failed


def pageindex_market(market: str, lakehouse: Path, offset: int, limit: int,
                     force: bool) -> list[str]:
    """为一个市场下所有公司目录补 pageindex，返回失败股票代码列表。"""
    market_dir = lakehouse / market
    if not market_dir.is_dir():
        log.warning("[%s] 目录不存在: %s（请先运行 uv run python cagent/scraper/cli.py"
                    " + uv run python cagent/ocr/cli.py）", market, market_dir)
        return []
    companies = sorted(p.name for p in market_dir.iterdir()
                       if p.is_dir() and (p / "index.json").exists())
    total_all = len(companies)
    companies = companies[offset:offset + limit if limit > 0 else None]
    if not companies:
        log.warning("[%s] 无公司可处理（offset=%d, limit=%d，共 %d 家）",
                    market, offset, limit, total_all)
        return []

    log.info("=== 市场 %s: 处理第 %d~%d 家 / 共 %d 家 ===",
             market, offset + 1, offset + len(companies), total_all)
    failed = []
    for i, code in enumerate(companies, 1):
        log.info("[%s %d/%d] %s", market, i, len(companies), code)
        try:
            fail = pageindex_company(market, code, lakehouse, force=force)
        except Exception as exc:
            log.error("  [FAIL] %s（%s）", code, exc)
            failed.append(code)
            continue
        if fail:
            log.error("  [FAIL] %s（%d 份 pageindex 构建失败）", code, fail)
            failed.append(code)

    log.info("=== 市场 %s 完成: %d 家处理, %d 家失败 ===",
             market, len(companies), len(failed))
    return failed