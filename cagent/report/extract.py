"""合并三大主表提取 cli：

    # 提取某公司 derived/ 下所有年报的合并三大主表，已有的跳过
    uv run python -m cagent.report.extract --stock 601633 --market cn

    # 单文件调试
    uv run python -m cagent.report.extract -i .cagent/cn/601633/derived/2026-03-28_1225047452.md

幂等断点续跑：<stem>.statements/ 目录已存在则跳过，重跑同一命令即可续跑。

分工（见 docs/reader-plan.md）：通过 TOC 选择财报章节；程序给表格单元格编号，模型只选择科目与
当期单元格，程序直读数值并做原文核验。RLM 只对固定合计目标配平，失败仅告警。

产物布局（每年报一个目录，关联信息后续并列加入）：
    derived/<stem>.statements/
    ├── meta.json   # 年报文件名、币种、金额单位、通用备注、三张表的页码范围
    ├── bs.json     # 合并资产负债表 {"title", "start_page", "end_page", "rows", "source_checks"}
    ├── is.json     # 合并利润表
    └── cf.json     # 合并现金流量表
rows 含 item/note/value/raw_value 及 page/table/row/column 原文坐标。
"""

import argparse
import json
import logging
import sys
from pathlib import Path

from cagent.core.agent import Agent
from cagent.core.chunk import Toc
from cagent.core.storage import atomic_write
from cagent.report.check import check_statement
from cagent.report.source import grid_pages, materialize_rows, verify_rows

logger = logging.getLogger(__name__)

_TITLES = {"bs": "合并资产负债表", "is": "合并利润表", "cf": "合并现金流量表"}


def _locate_statements(agent: Agent, toc: Toc) -> dict[str, tuple[int, int]]:
    """先通过 TOC 选章节，再在章节内定位三张合并报表的物理页码范围。"""
    indexes = toc.select_chapters("找出包含合并资产负债表、合并利润表和合并现金流量表的章节")
    schema = {
        "type": "object",
        "properties": {
            "statements": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": ["bs", "is", "cf"]},
                        "start": {"type": "integer"},
                        "end": {"type": "integer"},
                    },
                    "required": ["kind", "start", "end"],
                },
            }
        },
        "required": ["statements"],
    }
    found = []
    for index in indexes:
        logger.info(f"extract table from {toc.chapters[index]["title"]}")
        for chunk in toc.get_chapter_chunks_by_index(index, budget=32768):
            prompt = f"""在原文中定位合并资产负债表、合并利润表、合并现金流量表的完整页码范围。
返回 bs、is、cf 的 start 和 end，使用 <!-- page N --> 的物理页码；没有的报表不要返回。

<pages>
{chunk}
</pages>"""
            found += agent.chat_json(prompt, schema)["statements"]
            if {s["kind"] for s in found} == set(_TITLES):
                break
        if {s["kind"] for s in found} == set(_TITLES):
            break
    ranges = {}
    for s in found:
        start, end = s["start"], s["end"]
        if not (0 < start <= end <= len(toc.pages)):
            raise ValueError(f"模型返回的页码越界: {s}")
        ranges[s["kind"]] = (start, end)
    if len(ranges) != 3:
        raise ValueError(f"三张合并报表未找全，只定位到: {sorted(ranges)}")
    return ranges


def _selection_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "rows": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "item": {"type": "string", "description": "纯科目名，不带序号或列示前缀"},
                        "note": {"type": "string", "description": "附注编号，没有则为空字符串"},
                        "page": {"type": "integer"},
                        "table": {"type": "integer"},
                        "item_cell": {"type": "string", "description": "科目单元格，如 r1c0"},
                        "value_cell": {"type": "string", "description": "当期金额单元格，无金额则为空字符串"},
                    },
                    "required": ["item", "note", "page", "table", "item_cell", "value_cell"],
                },
            }
        },
        "required": ["rows"],
    }


def _extract_prompt(kind: str, start: int, end: int, pages: list[str]) -> str:
    title = _TITLES[kind]
    grid = grid_pages(pages[start - 1 : end], start)
    return f"""这是财务报表提取。逐行选择「{title}」的科目和当期金额单元格：
- item 只保留纯科目名，去掉序号和“其中/加/减”等列示前缀，例如“(一)基本每股收益”输出“基本每股收益”，“其中:利息费用”输出“利息费用”。
- note 填附注编号；不要输出金额，value_cell 由程序取值。
- page/table 使用原文标号，item_cell/value_cell 使用程序给出的 r行c列坐标。
- 无金额时 value_cell 留空；表头和单位不要输出；按原文行序输出。

<grid>
{grid}
</grid>"""


def _current_columns(agent: Agent, grid: str) -> dict[tuple[int, int], int]:
    schema = {
        "type": "object",
        "properties": {
            "columns": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"page": {"type": "integer"}, "table": {"type": "integer"}, "column": {"type": "integer"}},
                    "required": ["page", "table", "column"],
                },
            }
        },
        "required": ["columns"],
    }
    prompt = f"为每个表格选择唯一的当期金额列 column；不要选上年列。\n\n{grid}"
    selected = agent.chat_json(prompt, schema)["columns"]
    return {(item["page"], item["table"]): item["column"] for item in selected}


def _extract_rows(agent: Agent, kind: str, start: int, end: int, pages: list[str]) -> list[dict]:
    source_pages = pages[start - 1 : end]
    grid = grid_pages(source_pages, start)
    current_columns = _current_columns(agent, grid)
    selections = agent.chat_json(_extract_prompt(kind, start, end, pages), _selection_schema(), max_tokens=16384)["rows"]
    review = f"""对照带坐标原文复核科目和当期单元格选择，修正后返回完整 rows。
每个表只能使用以下当期列：{current_columns}。item 去掉序号和列示前缀。
不要输出金额，不要把不同期间拆成重复科目，不要空科目名或表头。

<rows>
{json.dumps(selections, ensure_ascii=False)}
</rows>

<grid>
{grid}
</grid>"""
    reviewed = agent.chat_json(review, _selection_schema(), max_tokens=16384)["rows"]
    return materialize_rows(reviewed, source_pages, start, current_columns)


def _repair_rows(agent: Agent, rows: list[dict], mismatches: list[dict], source_pages: list[str], start: int) -> list[dict]:
    selections = [
        {
            "item": row["item"],
            "note": row["note"],
            "page": row["page"],
            "table": row["table"],
            "item_cell": f"r{row['row']}c{row['column']}",
            "value_cell": f"r{row['row']}c{row['column']}" if row["value"] is not None else "",
        }
        for row in rows
    ]
    prompt = f"""以下是单元格核验失败。对照带坐标原文修正选择，返回完整 rows，不要输出金额。

<rows>
{json.dumps(selections, ensure_ascii=False)}
</rows>

<mismatches>
{json.dumps(mismatches, ensure_ascii=False)}
</mismatches>

<grid>
{grid_pages(source_pages, start)}
</grid>"""
    reviewed = agent.chat_json(prompt, _selection_schema(), max_tokens=16384)["rows"]
    return materialize_rows(reviewed, source_pages, start)


def _extract_metadata(agent: Agent, ranges: dict[str, tuple[int, int]], pages: list[str]) -> dict:
    headers = "\n\n".join(pages[start - 1] for start, _ in sorted(ranges.values()))
    schema = {
        "type": "object",
        "properties": {"currency": {"type": "string"}, "unit": {"type": "string"}, "note": {"type": "string"}},
        "required": ["currency", "unit", "note"],
    }
    return agent.chat_json(f"这是财务报表元数据提取。从报表表头分别提取货币币种、金额单位和其他备注，使用报表原文；没有备注则 note 留空。\n\n{headers}", schema)


def extract_file(agent: Agent, md_path: Path, force: bool = False) -> Path | None:
    """提取单个年报 md，产物写在 derived/<stem>.statements/；已存在且非 force 则跳过。"""
    out_dir = md_path.with_suffix(".statements")
    if out_dir.exists() and not force:
        logger.info(f"跳过（已存在）: {out_dir.name}")
        return None
    toc = Toc(agent, md_path)
    pages = toc.pages
    ranges = _locate_statements(agent, toc)
    logger.info(f"定位: {md_path.name} " + ", ".join(f"{k}={v[0]}-{v[1]}" for k, v in ranges.items()))

    metadata = _extract_metadata(agent, ranges, pages)
    out_dir.mkdir(parents=True, exist_ok=True)
    atomic_write(out_dir / "meta.json", (json.dumps({"file": md_path.name, **metadata, "ranges": {k: list(v) for k, v in ranges.items()}}, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
    for kind, (start, end) in sorted(ranges.items()):
        source_pages = pages[start - 1 : end]
        rows = _extract_rows(agent, kind, start, end, pages)
        for attempt in range(3):
            source_issues = verify_rows(rows, source_pages, start)
            if not source_issues:
                break
            logger.warning(f"  {kind} 第 {attempt + 1} 轮有 {len(source_issues)} 项原文核验失败，反馈修正")
            if attempt < 2:
                rows = _repair_rows(agent, rows, source_issues, source_pages, start)
        checks = check_statement(agent, rows, kind)
        failed = [check for check in checks if not check["ok"]]
        if failed:
            logger.warning(f"  {kind} 有 {len(failed)} 项配平告警: {[check['item'] for check in failed]}")
        payload = {
            "title": _TITLES[kind],
            "start_page": start,
            "end_page": end,
            "n_rows": len(rows),
            "rows": rows,
            "source_checks": source_issues,
            "source_passed": f"{len(rows) - len(source_issues)}/{len(rows)}",
            "checks": checks,
        }
        atomic_write(out_dir / f"{kind}.json", (json.dumps(payload, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
        logger.info(f"  {kind}: {len(rows)} 行, 原文核验 {payload['source_passed']}")
    return out_dir


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )

    parser = argparse.ArgumentParser(description="提取合并三大主表 statements.json")
    parser.add_argument("--stock", help="股票代码，如 601633、09863")
    parser.add_argument("--market", choices=["cn", "hk"], help="目标市场")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[2] / ".cagent"))
    parser.add_argument("--force", action="store_true", help="重建已存在的 statements 目录")
    parser.add_argument("-i", "--input", help="单文件调试模式，指定 md 文件路径")
    args = parser.parse_args()
    agent = Agent(trace=True)

    if args.input:
        extract_file(agent, Path(args.input), args.force)
        sys.exit(0)

    md_dir = Path(args.root) / str(args.market) / str(args.stock) / "derived"
    mds = sorted(md_dir.glob("*.md"))
    if not mds:
        logger.error(f"{md_dir} 没有解析产物，先跑 ocr/cli.py")
        sys.exit(1)
    failed = []
    for md in mds:
        try:
            extract_file(agent, md, args.force)
        except Exception:
            logger.exception(f"提取失败: {md.name}")
            failed.append(md.name)
    if failed:
        logger.error(f"失败清单: {failed}")
        sys.exit(1)
