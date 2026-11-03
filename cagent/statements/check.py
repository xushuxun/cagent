"""严格校验三表恒等式：uv run python -m cagent.statements.check -i <statement.json>"""

import argparse
import json
import logging
import sys
from decimal import Decimal
from pathlib import Path

from cagent.core.agent import Agent
from cagent.statements.check_fewshots import BALANCE_SHEET, CASH_FLOW, INCOME_STATEMENT

logger = logging.getLogger(__name__)

_FORMULAS = {
    "balance_sheet": """
- 流动/非流动资产合计 = 该分类下实际列示的直接组成项之和。
- 资产总计 = 流动资产合计 + 非流动资产合计。
- 流动/非流动负债合计 = 该分类下实际列示的直接组成项之和。
- 负债合计 = 流动负债合计 + 非流动负债合计。
- 归母权益合计 = 实际列示的权益组成项按列示方向计算；组成项可增减。
- 权益合计 = 归母权益 + 少数股东权益；负债和权益总计 = 负债合计 + 权益合计。
- 若有净额行：流动资产净额 = 流动资产 - 流动负债；资产净额 = 资产总值 - 负债总额。
""",
    "income_statement": """
- 毛利 = 收入 + 带符号销售成本。
- 营业利润/经营亏损 = 收入或毛利 + 各收益及费用按报表列示符号计算；组成项可增减。
- 利润总额 = 营业利润 + 营业外收入 - 营业外支出。
- 净利润 = 利润总额 - 所得税费用；若所得税以负数列示，则按带符号值相加。
- 净利润也等于持续/终止经营分类之和，或归母净利润 + 少数股东损益。
- 其他综合收益 = 实际列示的直接分类或归属项之和。
- 综合收益总额 = 净利润 + 其他综合收益，也等于归母综合收益 + 少数股东综合收益。
""",
    "cash_flow": """
- 经营/投资/筹资现金流入或流出小计 = 该类实际直接现金流项目之和；项目可增减。
- 某类现金流净额 = 流入小计 - 流出小计；没有小计时按实际带符号现金流项目求和。
- 现金净增加/减少额 = 经营、投资、筹资现金流净额之和，并按报表结构决定是否另含汇率影响。
- 期末现金 = 期初现金 + 现金净增加/减少额；若汇率影响未计入净额，则期末等式另加汇率影响。
""",
}


def _parent_index(rows: list[dict], index: int) -> int | None:
    parent = rows[index]["parent"]
    if not parent:
        return None
    candidates = [i for i, row in enumerate(rows) if i != index and row["item"] == parent]
    if not candidates:
        raise ValueError(f"父级不存在: {rows[index]}")
    previous = [i for i in candidates if i < index]
    return previous[-1] if previous else min(candidates, key=lambda i: abs(i - index))


def statement_paths(rows: list[dict]) -> list[list[str]]:
    paths: dict[int, list[str]] = {}

    def build(index: int, seen: tuple[int, ...] = ()) -> list[str]:
        if index in paths:
            return paths[index]
        if index in seen:
            raise ValueError(f"父级形成循环: {rows[index]}")
        parent = _parent_index(rows, index)
        if parent is None:
            return [rows[index]["item"]]
        path = [*build(parent, (*seen, index)), rows[index]["item"]]
        paths[index] = path
        return path

    return [build(index) for index in range(len(rows))]


def _resolve(rows: list[dict], ref: dict) -> int:
    matches = [index for index, row in enumerate(rows) if row["item"] == ref["item"] and row["parent"] == ref["parent"]]
    if not matches:
        matches = [index for index, row in enumerate(rows) if row["item"] == ref["item"]]
    if len(matches) != 1:
        raise ValueError(f"科目无法唯一定位: {ref}")
    return matches[0]


def _report(rows: list[dict]) -> str:
    return "\n".join(f'<row item="{row["item"]}" parent="{row["parent"]}">{row["value"]}</row>' for row in rows)


def check_statement(agent: Agent, rows: list[dict], kind: str) -> list[dict]:
    examples = {"balance_sheet": BALANCE_SHEET, "income_statement": INCOME_STATEMENT, "cash_flow": CASH_FLOW}[kind]
    prompt = f"""为 report 中完整、明确的恒等式选择科目。
sum 用 item+parent 引用合计行；add/sub 按父级分组，items 只列科目名，不要重复父级。
每个 JSON 对象中的键只能出现一次，尤其不得重复输出 add 或 sub。
只使用以下通用公式，组成项按公司实际报表增减，不要把局部明细凑成等式或重复加总。
<formulas>{_FORMULAS[kind]}</formulas>

<examples>{examples}</examples>

<report>
{_report(rows)}
</report>"""
    side = {"type": "object", "properties": {"item": {"type": "string"}, "parent": {"type": "string"}}, "required": ["item", "parent"]}
    group = {"type": "object", "properties": {"parent": {"type": "string"}, "items": {"type": "array", "items": {"type": "string"}}}, "required": ["parent", "items"]}
    schema = {
        "type": "object",
        "properties": {
            "checks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "sum": side,
                        "add": {"type": "array", "items": group},
                        "sub": {"type": "array", "items": group},
                    },
                    "required": ["sum", "add", "sub"],
                },
            }
        },
        "required": ["checks"],
    }
    for attempt in range(2):
        try:
            marked = agent.chat_json(prompt, schema, max_tokens=8192)["checks"]
            break
        except RuntimeError as error:
            if "structured_output_failed" not in str(error) or attempt == 1:
                raise
            logger.warning("结构化输出失败，原请求重试一次")
    checks = []
    for check in marked: # pyright: ignore[reportPossiblyUnboundVariable]
        sum_index = _resolve(rows, check["sum"])
        add = [_resolve(rows, {"item": item, "parent": group["parent"]}) for group in check["add"] for item in group["items"]]
        sub = [_resolve(rows, {"item": item, "parent": group["parent"]}) for group in check.get("sub", []) for item in group["items"]]
        indexes = [sum_index, *add, *sub]
        if len(indexes) != len(set(indexes)):
            raise ValueError(f"等式重复引用科目: {check}")
        if not add and not sub:
            raise ValueError(f"等式没有组成项: {check}")
        if rows[sum_index]["value"] is None:
            raise ValueError(f"合计行为空: {check}")
        computed = sum((Decimal(str(rows[index]["value"] or 0)) for index in add), Decimal(0))
        computed -= sum((Decimal(str(rows[index]["value"] or 0)) for index in sub), Decimal(0))
        reported = Decimal(str(rows[sum_index]["value"]))
        equation = f"{rows[sum_index]['item']} = " + " + ".join(rows[index]["item"] for index in add) + (" - " + " - ".join(rows[index]["item"] for index in sub) if sub else "")
        if computed != reported:
            raise ValueError(f"配平失败: {equation}，计算值 {computed}，报表值 {reported}")
        checks.append({"target": rows[sum_index]["item"], "equation": equation, "computed": str(computed), "reported": str(reported), "ok": True})
    return checks


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S", stream=sys.stderr)
    parser = argparse.ArgumentParser(description="严格校验一张报表的恒等式")
    parser.add_argument("-i", "--input", type=Path, required=True)
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    checks = check_statement(Agent(trace=True), payload["rows"], args.input.stem)
    for check in checks:
        logger.info(f"✓ {check['equation']} ({check['computed']} = {check['reported']})")
    logger.info(f"{len(checks)} 项恒等式全部通过")
