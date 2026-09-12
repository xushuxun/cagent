"""lakehouse（公告文件库）读取层：公告定位、页文本切分。

目录布局约定见 lakehouse/README.md：

    <lakehouse>/<market>/<code>/           # 如 <仓库根>/.cagent/hk/09863/
        index.json                         # 公司信息 + 公告元数据（日期升序）
        derived/<ISO日期>_<原文件名>.md     # OCR 全文（页间用 <!-- page N --> 分隔）

本层只读不写，不碰 LLM（无 dspy 依赖）。页码索引（pageindex）的构建与读取都在
cagent/pageindex（builder.py 构建，reader.py 读取/切分）。
"""

import json
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

# 全局默认 lakehouse 根：仓库根下的 .cagent/（可用参数覆盖）
DEFAULT_LAKEHOUSE = Path(__file__).resolve().parents[2] / ".cagent"

PAGE_RE = re.compile(r"\s*<!--\s*page\s+(\d+)\s*-->")
# 渲染一页比页面正文多出的字符（首尾两个标记 + 换行）：按页凑段长时要用它算开销
PAGE_MARK_CHARS = len("<!-- page 8888 -->\n\n\n<!-- /page 8888 -->")
# 单页解析失败的占位符：解析端写、报告端读进异常清单，同一份约定只在这里定义
PAGE_FAILURE_RE = re.compile(r">\s*⚠️\s*第\s+(\d+)\s+页解析失败", re.MULTILINE)


def page_failure_text(page: int) -> str:
    """第 page 页解析失败时写进解析全文的占位符（与 PAGE_FAILURE_RE 配对）。"""
    return f"> ⚠️ 第 {page} 页解析失败，本页内容缺失。"


def page_failures(md: Path) -> list[int]:
    """解析全文里标记为「本页内容缺失」的物理页号；文件不存在返回空。"""
    if not md.exists():
        return []
    return [int(n) for n in PAGE_FAILURE_RE.findall(
        md.read_text(encoding="utf-8", errors="replace"))]


def is_annual_report(title: str) -> bool:
    """公告标题 → 是否年报正文。下载层排队、解析层选档、报告层选材共用同一判定标准。

    只认标题里的年报词（年報/年报/年度報告/年度报告/Annual Report）；摘要、英文版、
    Abridged 明确排除；空标题保守保留（元数据缺失不误杀）。通知信函、ESG/可持续报告
    等标题天然不含年报词，自然被滤除。
    """
    t = (title or "").strip()
    tl = t.lower()
    if not t:
        return True
    if any(k in t for k in ("摘要", "英文", "English", "Abridged")) \
            or any(k in tl for k in ("english", "abridged")):
        return False
    return (any(k in t for k in ("年報", "年报", "年度報告", "年度报告"))
            or "annual report" in tl)


class LakehouseReader:
    """lakehouse 纯数据读取器。

    只负责从 lakehouse 目录中定位公司与公告、按物理页码切分文本。
    所有方法均为无状态读操作，实例本身只保存根目录路径。
    """

    def __init__(self, root: Path = DEFAULT_LAKEHOUSE):
        self.root = Path(root)

    def company_dir(self, market: str, code: str) -> Path:
        """某公司在 lakehouse 中的目录。"""
        return self.root / market / code

    def list_filings(self, data_dir: Path) -> list[dict]:
        """某公司 derived/ 下全部 OCR 公告，文件名升序（日期前缀即时间序）。

        每条含 file/date/title/bytes/path；date/title 优先取 index.json 元数据，
        缺省时按文件名兜底。
        """
        derived = Path(data_dir) / "derived"
        meta: dict[str, dict] = {}
        index = Path(data_dir) / "index.json"
        if index.exists():
            idx = json.loads(index.read_text(encoding="utf-8"))
            for f in idx.get("filings", []):
                meta[Path(f["file"]).with_suffix(".md").name] = f
        out = []
        for p in sorted(derived.glob("*.md")):
            m = meta.get(p.name, {})
            out.append({
                "file": p.name,
                "date": m.get("date", p.name[:10]),
                "title": m.get("title", p.stem),
                "bytes": p.stat().st_size,
                "path": p,
            })
        return out

    def pages_of(self, text: str) -> dict[int, str]:
        """按 <!-- page N --> 标记把全文切分为 {物理页码: 页面内容}。"""
        pages: dict[int, str] = {}
        marks = list(PAGE_RE.finditer(text))
        for i, m in enumerate(marks):
            end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
            pages[int(m.group(1))] = text[m.end():end].strip()
        return pages

    def render_pages(self, pages: dict[int, str], start: int, end: int) -> str:
        """把 [start, end] 闭区间内的页面渲染回带 <!-- page N --> 标记的文本。

        每页块尾再补一个 `<!-- /page N -->`：年报页脚印着另一套印刷页码（物理 p.119 的页脚
        印 "117"），笔记只该认 `<!-- page N -->`，但模型会顺手抄块尾最后看到的数字——把物理
        页码也放到块尾，抄错的机会就没了。`/page` 不被 PAGE_RE 匹配，所以 pages_of 回读时
        页号照旧一一对应（该标记并入所属页的内容尾部，无害：没有任何环节把渲染结果再切页）。
        """
        return "\n\n".join(f"<!-- page {n} -->\n\n{pages[n]}\n<!-- /page {n} -->"
                           for n in range(start, end + 1) if n in pages)
