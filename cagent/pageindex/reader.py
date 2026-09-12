"""页码索引（pageindex）读取：读 `.pageindex.json` 构建产物，切成章节阅读单元。

页码索引是文档的构建产物（与 OCR markdown 同级）：落盘 md 旁的 `.pageindex.json`，
缺失/损坏/与原文页数不一致时重建（构建在同包 `builder.py`，批量入口在同包
`cli.py`）。本模块只读、不碰 LLM。
"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from cagent.lib.lakehouse import PAGE_MARK_CHARS, LakehouseReader

PAGEINDEX_SUFFIX = ".pageindex.json"


def pageindex_is_current(md: Path, reader: LakehouseReader | None = None) -> bool:
    """pageindex 是否与解析全文一致：存在、可解析、结构正确、页数与原文一致。"""
    reader = reader or LakehouseReader()
    idx_file = md.with_suffix(PAGEINDEX_SUFFIX)
    if not idx_file.exists():
        return False
    try:
        data = json.loads(idx_file.read_text(encoding="utf-8"))
        pages = reader.pages_of(md.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    return (isinstance(data, dict)
            and isinstance(data.get("structure"), list)
            and bool(pages)
            and data.get("pages") == max(pages)
            and (data.get("md_sha256") is None
                 or data.get("md_sha256") == hashlib.sha256(
                     md.read_bytes()).hexdigest()))


@dataclass
class PageIndexNode:
    """页码索引节点（load 摊平后的视图）：start/end 为物理页码。"""

    node_id: str
    title: str
    start: int
    end: int
    summary: str = ""


@dataclass
class ReadingUnit:
    """一个阅读单元：一个章节（或并章小组）的页码闭区间 + 该区间按 chunk_chars 切好的原文段。

    title 为空表示无目录可用时的整篇盲切兜底（start/end 无意义）；title 含「／」
    表示这是相邻小章节并章的一段；parts 多于一段时全部属于 title 那一章。
    """

    title: str
    start: int
    end: int
    parts: list[str]


class PageIndexReader:
    """某份 markdown 公告的页码索引读取器。

    读取 md 旁的 .pageindex.json（构建产物，由 cagent.pageindex.builder 产出），
    基于其叶节点把原文切成阅读单元。不依赖 LLM；需要页文本操作时复用传入的
    LakehouseReader。
    """

    def __init__(self, md_path: Path, reader: LakehouseReader | None = None):
        self.md_path = Path(md_path)
        self._reader = reader or LakehouseReader()
        self._tree: list[dict] | None = None

    def _load_tree(self) -> list[dict] | None:
        if self._tree is not None:
            return self._tree
        if not pageindex_is_current(self.md_path, self._reader):
            self._tree = []
            return None
        self._tree = json.loads(
            self.md_path.with_suffix(PAGEINDEX_SUFFIX).read_text(encoding="utf-8")
        )["structure"]
        return self._tree

    def load(self) -> list[PageIndexNode] | None:
        """读 .pageindex.json，摊平为 PageIndexNode 列表（文档序）；没有则 None。"""
        tree = self._load_tree()
        if tree is None:
            return None
        flat: list[PageIndexNode] = []

        def walk(nodes: list[dict], path: list[str]) -> None:
            for n in nodes:
                flat.append(PageIndexNode(n["node_id"], " > ".join(path + [n["title"]]),
                                          n["start_index"], n["end_index"],
                                          n.get("summary", "")))
                walk(n.get("sub_nodes") or [], path + [n["title"]])

        walk(tree, [])
        return flat

    def leaves(self) -> list[tuple[str, int, int]] | None:
        """叶节点 (带父链标题, 起始页, 结束页)，文档序；没有 pageindex 返回 None。"""
        tree = self._load_tree()
        if tree is None:
            return None
        out: list[tuple[str, int, int]] = []

        def walk(nodes: list[dict], path: list[str]) -> None:
            for n in nodes:
                subs = n.get("sub_nodes") or []
                if subs:
                    walk(subs, path + [n["title"]])
                else:
                    out.append((" > ".join(path + [n["title"]]),
                                n["start_index"], n["end_index"]))

        walk(tree, [])
        return out

    def reading_units(self, chunk_chars: int) -> list[ReadingUnit] | None:
        """按叶节点把全文切成章节阅读单元（文档序）；无页码标记或无 pageindex 则 None。

        切分边界落在章节上，不按定长盲切：
        - 叶节点区间之间/前后的空隙补为「未编入目录」区间，保证 1..末页每页恰好被读到一次；
        - 相邻叶节点页范围重叠时以前一章节为准；
        - 相邻小章节贪婪并章到 ≤chunk_chars；
        - 大章逐章按页切段，段永远不跨章。
        """
        pages = self._reader.pages_of(self.md_path.read_text(encoding="utf-8"))
        leaves = self.leaves()
        if not pages or not leaves:
            return None
        last = max(pages)
        spans: list[tuple[str, int, int]] = []
        cur = 1
        for title, s, e in leaves:
            s, e = max(s, cur), min(e, last)
            if e < s:
                continue
            if s > cur:
                spans.append(("未编入目录", cur, s - 1))
            spans.append((title, s, e))
            cur = e + 1
        if cur <= last:
            spans.append(("未编入目录", cur, last))

        units: list[ReadingUnit] = []
        group: list[tuple[str, int, int]] = []
        group_chars = 0

        def span_chars(s: int, e: int) -> int:
            return sum(len(pages[n]) + PAGE_MARK_CHARS for n in range(s, e + 1) if n in pages)

        def flush() -> None:
            if not group:
                return
            if group_chars <= chunk_chars:      # 小组并章：整组一段
                s, e = group[0][1], group[-1][2]
                units.append(ReadingUnit("／".join(t for t, _, _ in group), s, e,
                                         [self._reader.render_pages(pages, s, e)]))
            else:                               # 大组：逐章切段，段不跨章
                for title, s, e in group:
                    units.append(ReadingUnit(
                        title, s, e,
                        [self._reader.render_pages(pages, a, b)
                         for a, b in _page_spans(pages, s, e, chunk_chars)]))
            group.clear()

        for title, s, e in spans:
            chars = span_chars(s, e)
            if group and group_chars + chars > chunk_chars:
                flush()
                group_chars = 0
            group.append((title, s, e))
            group_chars += chars
        flush()
        return units


def _page_spans(pages: dict[int, str], start: int, end: int,
                chunk_chars: int) -> list[tuple[int, int]]:
    """把页区间按 ≤chunk_chars 切成连续子区间（每页恰好一次；单页超限则自成一段）。"""
    out: list[tuple[int, int]] = []
    s = e = size = 0
    for n in range(start, end + 1):
        zn = len(pages[n]) + PAGE_MARK_CHARS if n in pages else 0
        if not size:
            s, e, size = n, n, zn
        elif size + zn > chunk_chars:
            out.append((s, e))
            s, e, size = n, n, zn
        else:
            e, size = n, size + zn
    if size:
        out.append((s, e))
    return out