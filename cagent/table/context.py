"""为合并三大主表关联报表项目附注：

    uv run python -m cagent.table.context --stock 002594 --market cn
    uv run python -m cagent.table.context -i .cagent/cn/002594/derived/2025-03-25_1222881496.md

依赖同目录的 <stem>.statements/，产物为其中的 context.json；已存在则跳过，--force 重建。
模型只定位原文边界和建立语义关联，程序切取展示内容，不改写主表数值。
"""

import argparse
import json
import logging
import sys
from pathlib import Path

from cagent.core.agent import Agent
from cagent.core.chunk import Toc
from cagent.core.storage import atomic_write
from cagent.table.source import grid_pages

logger = logging.getLogger(__name__)
_KINDS = ("bs", "is", "cf")


def _entry_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "entries": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "start_page": {"type": "integer"},
                        "start": {"type": "string", "description": "该条目起始标题的原文，必须逐字复制"},
                        "end": {"type": "string", "description": "下一条目标题的原文，必须逐字复制"},
                    },
                    "required": ["title", "start_page", "start", "end"],
                },
            }
        },
        "required": ["entries"],
    }


def _locate_entries(agent: Agent, toc: Toc, rows: list[dict]) -> list[dict]:
    items = "、".join(dict.fromkeys(row["item"] for row in rows))
    indexes = toc.select_chapters("找出包含合并财务报表项目附注的章节")
    prompt = f"""从年报原文中找出与这些报表科目有关的「合并财务报表项目附注」：
{items}

- start_page 使用 <!-- page N --> 的物理页号。
- start 逐字复制该条目的起始标题，end 逐字复制紧随其后、标志本条目结束的下一个标题。
- 只返回本块中完整看到边界的条目；不要输出正文，不要关联科目，不要编造标题。

<pages>
{{chunk}}
</pages>"""
    entries = []
    for index in indexes:
        for chunk in toc.get_chapter_chunks_by_index(index):
            found = agent.chat_json(prompt.format(chunk=chunk), _entry_schema(), max_tokens=8192)["entries"]
            entries.extend(found)
    unique = {}
    for entry in entries:
        unique[(entry["start_page"], entry["start"])] = entry
    return list(unique.values())


def _page_span(text: str, page: int) -> tuple[int, int]:
    marker = f"<!-- page {page} -->"
    start = text.index(marker) + len(marker)
    next_marker = f"<!-- page {page + 1} -->"
    end = text.find(next_marker, start)
    return start, end if end != -1 else len(text)


def _materialize_entries(toc: Toc, entries: list[dict]) -> list[dict]:
    result = []
    for entry in entries:
        try:
            page_start, page_end = _page_span(toc.text, entry["start_page"])
            page_offset = toc.text[page_start:page_end].index(entry["start"])
            start = toc.text.rfind("\n", 0, page_start + page_offset) + 1
            heading = toc.text.index(entry["end"], start + len(entry["start"]))
            end = toc.text.rfind("\n", 0, heading) + 1
        except (ValueError, KeyError):
            logger.warning(f"跳过无法核对边界的条目: {entry}")
            continue
        result.append(
            {
                "id": f"note:{len(result)}",
                "title": entry["title"],
                "start_page": entry["start_page"],
                "content": toc.text[start:end].strip(),
            }
        )
    return result


def _associate_rows(agent: Agent, rows: list[dict], notes: list[dict], statements_grid: str) -> dict[str, dict]:
    entries = [{"id": entry["id"], "title": entry["title"], "start_page": entry["start_page"], "content": entry["content"][:500]} for entry in notes]
    schema = {
        "type": "object",
        "properties": {
            "rows": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "row_id": {"type": "string"},
                        "notes": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["row_id", "notes"],
                },
            }
        },
        "required": ["rows"],
    }
    prompt = f"""把报表科目关联到对应的附注条目。
- 根据科目含义、主表附注信息和条目标题判断；条目可能同时覆盖多个科目。
- 主表附注编号要同时读取表头章节与行内编号，例如“附注七”和行内“1”共同指向附注七、1；编号不完整时结合科目语义判断。
- 只关联合并财务报表附注；根据条目内容排除公司或母公司财务报表主要项目注释，即使科目同名。
- 没有可靠对应条目就返回空数组，不要强行关联。
- 只使用给出的 row_id 和条目 id；合计行、分类行通常无需关联。

<rows>
{json.dumps(rows, ensure_ascii=False)}
</rows>

<statements>
{statements_grid}
</statements>

<entries>
{json.dumps(entries, ensure_ascii=False)}
</entries>"""
    selected = agent.chat_json(prompt, schema, max_tokens=16384)["rows"]
    valid_rows = {row["row_id"] for row in rows}
    valid_ids = {entry["id"] for entry in entries}
    associations = {}
    for row in selected:
        if row["row_id"] not in valid_rows:
            continue
        associations[row["row_id"]] = {"notes": [entry_id for entry_id in row["notes"] if entry_id in valid_ids]}
    return {row["row_id"]: associations.get(row["row_id"], {"notes": []}) for row in rows}


def extract_context(agent: Agent, md_path: Path, force: bool = False) -> Path | None:
    statements_dir = md_path.with_suffix(".statements")
    out_path = statements_dir / "context.json"
    if out_path.exists() and not force:
        logger.info(f"跳过（已存在）: {out_path}")
        return None
    statements = {kind: json.loads((statements_dir / f"{kind}.json").read_text(encoding="utf-8")) for kind in _KINDS}
    rows = [
        {"row_id": f"{kind}:{index}", "statement": statement["title"], "item": row["item"]}
        for kind, statement in statements.items()
        for index, row in enumerate(statement["rows"])
    ]
    toc = Toc(agent, md_path)
    statements_grid = "\n".join(
        grid_pages(toc.pages[statement["start_page"] - 1 : statement["end_page"]], statement["start_page"]) for statement in statements.values()
    )
    entries = _locate_entries(agent, toc, rows)
    logger.info(f"定位到 {len(entries)} 个附注条目")
    notes = _materialize_entries(toc, entries)
    payload = {"notes": notes, "rows": _associate_rows(agent, rows, notes, statements_grid)}
    atomic_write(out_path, (json.dumps(payload, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
    logger.info(f"输出 {out_path}: {len(notes)} 个附注")
    return out_path


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S", stream=sys.stderr)
    parser = argparse.ArgumentParser(description="关联合并主表科目与报表项目附注")
    parser.add_argument("--stock")
    parser.add_argument("--market", choices=["cn", "hk"])
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[2] / ".cagent"))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("-i", "--input", type=Path)
    args = parser.parse_args()
    agent = Agent(trace=True)
    if args.input:
        extract_context(agent, args.input, args.force)
        sys.exit(0)
    md_dir = Path(args.root) / str(args.market) / str(args.stock) / "derived"
    mds = sorted(path for path in md_dir.glob("*.md") if path.with_suffix(".statements").is_dir())
    if not mds:
        logger.error(f"{md_dir} 没有已提取三表的年报")
        sys.exit(1)
    failed = []
    for md_path in mds:
        try:
            extract_context(agent, md_path, args.force)
        except Exception:
            logger.exception(f"关联失败: {md_path.name}")
            failed.append(md_path.name)
    if failed:
        logger.error(f"失败清单: {failed}")
        sys.exit(1)
