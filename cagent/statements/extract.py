"""用极简 agent loop 提取和校验合并三表：

uv run python -m cagent.statements.extract -i <annual.md>
"""

import argparse
import json
import logging
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

from cagent.core.agent import Agent
from cagent.core.chunk import Toc
from cagent.core.loop import AgentLoop, Tool
from cagent.core.storage import atomic_write

logger = logging.getLogger(__name__)
_TITLES = {"balance_sheet": "合并资产负债表", "income_statement": "合并利润表", "cash_flow": "合并现金流量表"}


def locate(agent: Agent, toc: Toc) -> dict[str, list[int]]:
    indexes = toc.select_chapters("找出包含合并三大财务报表或财务报告正文的章节，不要指标或管理层讨论")
    chapter_ranges = [toc.get_chapter_page_range(index) for index in indexes]
    first, last = min(start for start, _ in chapter_ranges), max(end for _, end in chapter_ranges)
    schema = {"type": "object", "properties": {"statements": {"type": "array", "items": {"type": "object", "properties": {"kind": {"type": "string", "enum": list(_TITLES)}, "start": {"type": "integer"}, "end": {"type": "integer"}}, "required": ["kind", "start", "end"]}}}, "required": ["statements"]}
    end = min(first + 15, last)
    while True:
        logger.info(f"定位物理页 {first}-{end}")
        prompt = f"""定位合并资产负债表、利润表和现金流量表的完整物理页范围。
kind 为 balance_sheet、income_statement、cash_flow；使用 <!-- page N -->。start 是标题页，end 是最后一行所在页。
不要公司或母公司报表，不按目录推算；续页必须包含。只返回当前页面内能看到标题和末行的报表。
<pages>{toc.page_window(first, end)}</pages>"""
        found = {}
        for item in agent.chat_json(prompt, schema)["statements"]:
            if not first <= item["start"] <= item["end"] <= end:
                raise ValueError(f"页码超出窗口: {item}")
            found[item["kind"]] = [item["start"], item["end"]]
        if len(found) == 3:
            return found
        if end == last:
            raise ValueError(f"三大合并报表未找全: {sorted(found)}")
        end = min(end + 8, last)


def amount(raw: str) -> Decimal | None:
    normalized = raw.strip().replace(",", "").replace("，", "")
    negative = normalized.startswith("(") and normalized.endswith(")")
    try:
        value = Decimal(normalized.strip("()")) if raw.strip() not in ("", "-", "—", "--") else None
    except InvalidOperation as error:
        raise ValueError(f"无效金额: {raw}") from error
    return -value if negative and value is not None else value


def flatten(tree: list[dict]) -> list[dict]:
    rows = []

    def visit(node: dict, parent_id: int | None) -> None:
        row_id = len(rows) + 1
        rows.append({"row_id": row_id, "parent_id": parent_id, "item": node["item"], "value": amount(node["value"]), "amount_kind": node["amount_kind"]})
        for child in node["children"]:
            visit(child, row_id)

    for root in tree:
        visit(root, None)
    return rows


def calculate(rows: list[dict], equations: list[dict]) -> list[dict]:
    by_id = {row["row_id"]: row for row in rows}
    results = []
    for equation in equations:
        ids = [equation["sum"], *equation["add"], *equation["sub"]]
        if any(row_id not in by_id for row_id in ids):
            raise ValueError(f"等式引用不存在的行: {equation}")
        if len(ids) != len(set(ids)):
            raise ValueError(f"等式重复引用行: {equation}")
        target = by_id[equation["sum"]]
        if not equation["add"] and not equation["sub"] or target["value"] is None:
            raise ValueError(f"等式无组成项或合计为空: {equation}")
        computed = sum((by_id[row_id]["value"] or Decimal(0) for row_id in equation["add"]), Decimal(0))
        computed -= sum((by_id[row_id]["value"] or Decimal(0) for row_id in equation["sub"]), Decimal(0))
        reported = target["value"]
        expression = f"{target['item']} = " + " + ".join(by_id[row_id]["item"] for row_id in equation["add"]) + (" - " + " - ".join(by_id[row_id]["item"] for row_id in equation["sub"]) if equation["sub"] else "")
        if computed != reported:
            raise ValueError(f"配平失败: {expression}，计算值 {computed}，报表值 {reported}")
        results.append({"equation": expression, "computed": str(computed), "reported": str(reported), "check": equation})
    return results


def statement_loop(agent: Agent, toc: Toc, out_dir: Path, kind: str, start: int, end: int, context: dict) -> None:
    source = toc.page_window(start, end)
    scale = {"元": 1, "千元": 1000, "万元": 10000, "百万元": 1000000, "亿元": 100000000}[context["presentation_unit"].replace("人民币", "").replace("人民幣", "").replace("单位：", "").replace("币种：", "").replace(" ", "")]
    saved = False
    current_tree = []

    def extract(feedback: str = "") -> dict:
        nonlocal current_tree
        node = {"type": "object", "properties": {"item": {"type": "string"}, "value": {"type": "string"}, "amount_kind": {"type": "string", "enum": ["monetary", "per_share", "ratio", "other"]}, "children": {"type": "array", "items": {"$ref": "#/$defs/node"}}}, "required": ["item", "value", "amount_kind", "children"]}
        schema = {"$defs": {"node": node}, "type": "object", "properties": {"tree": {"type": "array", "items": {"$ref": "#/$defs/node"}}}, "required": ["tree"]}
        prompt = f"""将{_TITLES[kind]}提取为完整嵌套树。保留全部行和真实层级，item 去掉序号及“其中/加/减”等前缀，value 复制本期金额。
amount_kind 为 monetary/per_share/ratio/other。{feedback}
<pages>{source}</pages>"""
        current_tree = agent.chat_json(prompt, schema, max_tokens=16384)["tree"]
        return {"rows": flatten(current_tree)}

    def check(equations: list[dict]) -> dict:
        rows = flatten(current_tree)
        for row in rows:
            if row["value"] is not None and row["amount_kind"] == "monetary":
                row["value"] *= scale
        checks = calculate(rows, equations)
        return {"ok": True, "checks": checks, "rows": rows}

    def save(checks: list[dict]) -> dict:
        nonlocal saved
        rows = flatten(current_tree)
        for row in rows:
            if row["value"] is not None and row["amount_kind"] == "monetary":
                row["value"] *= scale
        calculate(rows, [check["check"] for check in checks])
        payload = {"title": _TITLES[kind], **context, "start_page": start, "end_page": end, "tree": current_tree, "rows": rows, "checks": checks}
        atomic_write(out_dir / f"{kind}.json", (json.dumps(payload, ensure_ascii=False, indent=1, default=str) + "\n").encode("utf-8"))
        saved = True
        return {"saved": True, "rows": len(rows)}

    equation = {"type": "object", "properties": {"sum": {"type": "integer"}, "add": {"type": "array", "items": {"type": "integer"}}, "sub": {"type": "array", "items": {"type": "integer"}}}, "required": ["sum", "add", "sub"]}
    tools = [
        Tool("extract", "从原始页面生成或按 feedback 修正当前树", extract, {"type": "object", "properties": {"feedback": {"type": "string"}}}),
        Tool("check", "校验当前树；每个等式包含 sum/add/sub 行号", check, {"type": "object", "properties": {"equations": {"type": "array", "items": equation}}, "required": ["equations"]}),
        Tool("save", "重新校验并保存当前树；传入 check 返回的完整 checks 数组", save, {"type": "object", "properties": {"checks": {"type": "array", "items": {"type": "object"}}}, "required": ["checks"]}),
    ]
    prompt = f"""先调用 extract 读取「{_TITLES[kind]}」，再调用 check 校验，全部通过后调用 save。
等式使用 extract 返回的 row_id；只加直接组成项，不把“其中”重复加总。失败时分析差额，调用 extract 修正后重新 check。
报告上下文：{json.dumps(context, ensure_ascii=False)}。原始页面：
<pages>{source}</pages>"""
    answer = AgentLoop(agent, tools).run(prompt, max_turns=12)
    if not saved:
        raise RuntimeError(f"{_TITLES[kind]} 未保存: {answer}")
    logger.info(f"{_TITLES[kind]} 已保存")


def extract_file(agent: Agent, md_path: Path, force: bool = False) -> Path:
    out_dir = md_path.with_suffix(".statements")
    if not force and all((out_dir / f"{kind}.json").exists() for kind in _TITLES):
        logger.info(f"跳过（已存在）: {out_dir}")
        return out_dir
    toc = Toc(agent, md_path)
    ranges = locate(agent, toc)
    first, last = min(start for start, _ in ranges.values()), max(end for _, end in ranges.values())
    declarations = "\n".join(line for line in toc.text.splitlines() if "为单位" in line or "单位为" in line)
    schema = {"type": "object", "properties": {"year": {"type": "integer"}, "currency": {"type": "string"}, "presentation_unit": {"type": "string", "enum": ["元", "千元", "万元", "百万元", "亿元"]}}, "required": ["year", "currency", "presentation_unit"]}
    context = agent.chat_json(f"确认合并报表年度、ISO 币种和金额单位；presentation_unit 只能为元、千元、万元、百万元或亿元。阅读主表和附注单位声明。<pages>{toc.page_window(first, last)}</pages><declarations>{declarations}</declarations>", schema, max_tokens=256)
    out_dir.mkdir(parents=True, exist_ok=True)
    atomic_write(out_dir / "meta.json", (json.dumps({"file": md_path.name, "ranges": ranges, **context}, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
    for kind, (start, end) in ranges.items():
        statement_loop(agent, toc, out_dir, kind, start, end, context)
    return out_dir


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S", stream=sys.stderr)
    parser = argparse.ArgumentParser(description="用 agent loop 提取合并三大主表")
    parser.add_argument("-i", "--input", type=Path, required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    extract_file(Agent(trace=True), args.input, args.force)
