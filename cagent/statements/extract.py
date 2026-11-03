"""定位并提取合并三大主表，逐表执行严格恒等式校验：

    uv run python -m cagent.statements.extract -i .cagent/cn/601633/derived/2026-03-28_1225047452.md
    uv run python -m cagent.statements.extract

不带 -i 时每家公司选择最新一份年报。已有三表产物则跳过，--force 重建。
"""

import argparse
import json
import logging
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

from cagent.core.agent import Agent
from cagent.core.chunk import Toc
from cagent.core.storage import atomic_write
from cagent.statements.check import check_statement
from cagent.statements.extract_fewshots import BALANCE_SHEET, CASH_FLOW, INCOME_STATEMENT

logger = logging.getLogger(__name__)

_TITLES = {
    "balance_sheet": "合并资产负债表",
    "income_statement": "合并利润表",
    "cash_flow": "合并现金流量表",
}


def _locate_statements(agent: Agent, toc: Toc) -> dict[str, list[int]]:
    logger.info("选择财务报告章节")
    task = """找出包含合并三大财务报表或财务报告正文的章节。
示例：候选为“第二節 公司簡介和主要財務指標”“第四節 管理層討論與分析”“第十節 財務報告（審計報告）”时，
只选“第十節 財務報告（審計報告）”；前两章只有指标和讨论，没有三大主表正文。"""
    indexes = toc.select_chapters(task)
    logger.info(f"选中章节 {[toc.chapters[index]['title'] for index in indexes]}")
    chapter_ranges = [toc.get_chapter_page_range(index) for index in indexes]
    first_page = min(start for start, _ in chapter_ranges)
    last_page = max(end for _, end in chapter_ranges)
    schema = {
        "type": "object",
        "properties": {
            "statements": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": list(_TITLES)},
                        "start": {"type": "integer"},
                        "end": {"type": "integer"},
                    },
                    "required": ["kind", "start", "end"],
                },
            }
        },
        "required": ["statements"],
    }
    window_end = min(first_page + 15, last_page)
    while True:
        logger.info(f"读取物理页 {first_page}-{window_end}")
        prompt = f"""一次定位合并资产负债表、合并利润表、合并现金流量表的完整物理页码范围。
kind 分别为 balance_sheet、income_statement、cash_flow；start 是报表标题首次出现的页，end 是报表最后一行所在页。
页码使用 <!-- page N --> 的 N，不要使用页脚印刷页码。
只定位合并报表，不要公司、母公司或银行自身报表。简体、繁体及英文合并报表标题具有同等含义。
续页没有标题也要包含；同页出现下一张报表不影响上一张的 end。
只返回当前页面范围内能看到标题和最后一行的报表；尚未看到完整报表时不要返回或推算。

<pages>
{toc.page_window(first_page, window_end)}
</pages>"""
        statements = agent.chat_json(prompt, schema)["statements"]
        logger.info(f"返回 {statements}")
        ranges = {}
        for statement in statements:
            kind, start, end = statement["kind"], statement["start"], statement["end"]
            if not first_page <= start <= end <= window_end:
                raise ValueError(f"报表页码超出当前窗口: {statement}")
            ranges[kind] = [start, end]
        if len(ranges) == len(_TITLES):
            return ranges
        if window_end == last_page:
            raise ValueError(f"三大合并报表未找全: {sorted(ranges)}")
        window_end = min(window_end + 8, last_page)


def _extract_rows(agent: Agent, toc: Toc, kind: str, start: int, end: int) -> list[dict]:
    title = _TITLES[kind]
    schema = {
        "type": "object",
        "properties": {
            "rows": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "item": {"type": "string"},
                        "value": {"type": "string", "description": "当期金额原文；无金额为空字符串"},
                        "parent": {"type": "string", "description": "直接父级纯科目名；根级为空字符串"},
                    },
                    "required": ["item", "value", "parent"],
                },
            }
        },
        "required": ["rows"],
    }
    examples = {"balance_sheet": BALANCE_SHEET, "income_statement": INCOME_STATEMENT, "cash_flow": CASH_FLOW}[kind]
    prompt = f"""直接解析 HTML，提取「{title}」的完整当期报表，只输出 item/value/parent。
排除同页其他报表、表头和单位；分类行保留，按原文行序输出，不省略科目行。
item 去掉序号和“其中/加/减”等列示前缀，其他名称保持原文。value 逐字复制当期金额，不选上年列。

<examples>{examples}</examples>

<pages>
{toc.page_window(start, end)}
</pages>"""
    result = agent.chat_json(prompt, schema, max_tokens=16384)["rows"]
    if not result:
        raise ValueError(f"{title} 未提取到行")
    rows = []
    for row in result:
        if row["parent"] and not any(candidate["item"] == row["parent"] for candidate in result):
            raise ValueError(f"父级不存在: {row}")
        raw = row["value"].strip()
        normalized = raw.replace(",", "").replace("，", "")
        negative = normalized.startswith("(") and normalized.endswith(")")
        try:
            value = Decimal(normalized.strip("()")) if raw not in ("", "-", "—", "--") else None
        except InvalidOperation as error:
            raise ValueError(f"无效金额: {row}") from error
        if negative and value is not None:
            value = -value
        rows.append({"item": row["item"], "value": int(value) if value is not None and value == value.to_integral() else float(value) if value is not None else None, "parent": row["parent"]})
    return rows


def extract_file(agent: Agent, md_path: Path, force: bool = False) -> Path | None:
    out_dir = md_path.with_suffix(".statements")
    if not force and all((out_dir / f"{kind}.json").exists() for kind in _TITLES):
        logger.info(f"跳过（已存在）: {out_dir}")
        return None
    logger.info(f"读取 TOC: {md_path.name}")
    toc = Toc(agent, md_path)
    logger.info(f"TOC 就绪: {toc.n_pages} 页，{len(toc.chapters)} 章")
    out_path = out_dir / "meta.json"
    if out_path.exists() and not force:
        ranges = json.loads(out_path.read_text(encoding="utf-8"))["ranges"]
    else:
        ranges = _locate_statements(agent, toc)
        out_dir.mkdir(parents=True, exist_ok=True)
        payload = {"file": md_path.name, "ranges": ranges}
        atomic_write(out_path, (json.dumps(payload, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
        logger.info(f"定位结果已写入: {out_path}")
    for kind, (start, end) in ranges.items():
        logger.info(f"提取 {_TITLES[kind]}，物理页 {start}-{end}")
        rows = _extract_rows(agent, toc, kind, start, end)
        logger.info(f"{_TITLES[kind]}: {len(rows)} 行，开始恒等式校验")
        checks = check_statement(agent, rows, kind)
        payload = {"title": _TITLES[kind], "start_page": start, "end_page": end, "rows": rows, "checks": checks}
        statement_path = out_dir / f"{kind}.json"
        atomic_write(statement_path, (json.dumps(payload, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
        logger.info(f"{_TITLES[kind]}: {len(checks)} 项恒等式全部通过，已写入 {statement_path}")
    return out_dir


def _input_files(root: Path, market: str | None, stock: str | None) -> list[Path]:
    pattern = f"{market or '*'}/{stock or '*'}/derived/*.md"
    latest = {}
    for path in sorted(root.glob(pattern)):
        latest[path.parent.parent] = path
    return list(latest.values())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S", stream=sys.stderr)
    parser = argparse.ArgumentParser(description="定位合并三大主表的物理页码")
    parser.add_argument("--stock")
    parser.add_argument("--market", choices=["cn", "hk"])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2] / ".cagent")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("-i", "--input", type=Path)
    args = parser.parse_args()
    agent = Agent(trace=True)

    if args.input:
        extract_file(agent, args.input, args.force)
        sys.exit(0)

    files = _input_files(args.root, args.market, args.stock)
    if not files:
        logger.error("没有解析产物，先运行 OCR")
        sys.exit(1)
    logger.info(f"待定位年报: {len(files)} 份")
    failed = []
    for file_number, md_path in enumerate(files, 1):
        logger.info(f"===== 年报 {file_number}/{len(files)}: {md_path} =====")
        try:
            extract_file(agent, md_path, args.force)
        except Exception:
            logger.exception(f"定位失败: {md_path}")
            failed.append(str(md_path))
    logger.info(f"定位完成: 成功 {len(files) - len(failed)}/{len(files)}")
    if failed:
        logger.error("失败清单:\n" + "\n".join(failed))
        sys.exit(1)
