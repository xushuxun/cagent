"""从已校验三表计算历史财务指标：

uv run python -m cagent.statements.metrics -s 601633 -s 002594
"""

import argparse
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from cagent.core.agent import Agent
from cagent.core.storage import atomic_write

_ROLES = ["revenue", "cost", "gross_profit", "profit", "parent_profit", "assets", "liabilities", "equity", "parent_equity", "current_assets", "current_liabilities", "operating_cash"]
_REQUIRED = {
    "balance_sheet": ["assets", "liabilities", "equity", "parent_equity", "current_assets", "current_liabilities"],
    "income_statement": ["revenue", "cost", "profit", "parent_profit"],
    "cash_flow": ["operating_cash"],
}


def align_roles(agent: Agent, payload: dict, kind: str) -> list[dict]:
    rows = [{"row_id": row["row_id"], "item": row["item"], "value": row["value"]} for row in payload["rows"]]
    item = {"type": "object", "properties": {"row_id": {"type": "integer"}, "role": {"type": "string", "enum": _ROLES}}, "required": ["row_id", "role"]}
    schema = {"type": "object", "properties": {"roles": {"type": "array", "items": item}}, "required": ["roles"]}
    prompt = f"""为财务指标计算对齐报表科目。只根据科目语义和报表结构标注，不按名称关键词做机械匹配。
角色含义：revenue 营业收入；cost 营业成本；gross_profit 毛利；profit 净利润；parent_profit 归母净利润；assets 资产总计；liabilities 负债合计；equity 权益合计；parent_equity 归母权益；current_assets 流动资产合计；current_liabilities 流动负债合计；operating_cash 经营现金流净额。
只返回实际存在且唯一的指标行，每行最多一个角色。必须覆盖该报表的全部必需角色。
<rows>{json.dumps(rows, ensure_ascii=False)}</rows>"""
    marked = agent.chat_json(prompt, schema, max_tokens=2048)["roles"]
    assigned = {entry["row_id"]: entry["role"] for entry in marked}
    for role in _REQUIRED[kind]:
        matches = [row_id for row_id, assigned_role in assigned.items() if assigned_role == role]
        if len(matches) != 1:
            raise ValueError(f"指标角色 {role} 未唯一匹配")
    return [{"row_id": row_id, "role": role} for row_id, role in assigned.items()]


def _value(rows: list[dict], role: str) -> Decimal:
    matches = [row["value"] for row in rows if row.get("role") == role]
    if len(matches) != 1 or matches[0] is None:
        raise ValueError(f"指标角色无效: {role}")
    return Decimal(str(matches[0]))


def _ratio(numerator: Decimal, denominator: Decimal) -> float | None:
    return float(numerator / denominator) if denominator else None


def company_metrics(agent: Agent, root: Path, market: str, stock: str) -> list[dict]:
    history: list[dict[str, Any]] = []
    for directory in sorted((root / market / stock / "derived").glob("*.statements")):
        payloads = {}
        for kind in _REQUIRED:
            path = directory / f"{kind}.json"
            if not path.exists():
                break
            payload = json.loads(path.read_text(encoding="utf-8"))
            if "year" not in payload:
                break
            roles = align_roles(agent, payload, kind)
            rows = [{**row, "role": next((entry["role"] for entry in roles if entry["row_id"] == row["row_id"]), None)} for row in payload["rows"]]
            payloads[kind] = {"rows": rows, "year": payload["year"], "currency": payload["currency"]}
        if len(payloads) != 3:
            continue
        income, balance, cash = (payloads[kind]["rows"] for kind in ["income_statement", "balance_sheet", "cash_flow"])
        revenue, cost = _value(income, "revenue"), _value(income, "cost")
        gross_profit = next((_value(income, "gross_profit") for row in income if row.get("role") == "gross_profit"), None)
        if gross_profit is None:
            gross_profit = revenue + cost if cost < 0 else revenue - cost
        history.append({
            "year": payloads["income_statement"]["year"],
            "currency": payloads["income_statement"]["currency"],
            "revenue": float(revenue),
            "gross_margin": _ratio(gross_profit, revenue),
            "net_margin": _ratio(_value(income, "profit"), revenue),
            "debt_ratio": _ratio(_value(balance, "liabilities"), _value(balance, "assets")),
            "current_ratio": _ratio(_value(balance, "current_assets"), _value(balance, "current_liabilities")),
            "operating_cash_to_profit": _ratio(_value(cash, "operating_cash"), _value(income, "profit")),
            "parent_profit": float(_value(income, "parent_profit")),
            "parent_equity": float(_value(balance, "parent_equity")),
        })
    history.sort(key=lambda row: row["year"])
    for index, row in enumerate(history):
        previous = history[index - 1] if index and history[index - 1]["year"] == row["year"] - 1 else None
        row["revenue_growth"] = (row["revenue"] - previous["revenue"]) / previous["revenue"] if previous else None
        average_equity = (Decimal(str(row["parent_equity"])) + Decimal(str(previous["parent_equity"]))) / 2 if previous else Decimal(str(row["parent_equity"]))
        row["roe"] = float(Decimal(str(row["parent_profit"])) / average_equity) if average_equity else None
    return history


def _percent(value: float | None) -> str:
    return "—" if value is None else f"{value:.2%}"


def _number(value: float | None) -> str:
    return "—" if value is None else f"{value:,.2f}"


def render(companies: dict[str, list[dict]]) -> str:
    lines = ["# 历史财务指标对比", "", "金额为亿元；同比只使用相邻年度；ROE 使用归母净利润除以平均归母权益。", ""]
    for company, rows in companies.items():
        lines.extend([f"## {company}", "", "| 年度 | 营收 | 营收增长 | 毛利率 | 净利率 | ROE | 资产负债率 | 流动比率 | 经营现金流/净利润 |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|"])
        for row in rows:
            values = [_number(row["revenue"] / 100000000), *[_percent(row[key]) for key in ["revenue_growth", "gross_margin", "net_margin", "roe", "debt_ratio"]], _number(row["current_ratio"]), _number(row["operating_cash_to_profit"])]
            lines.append(f"| {row['year']} | " + " | ".join(values) + " |")
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="计算并比较历史财务指标")
    parser.add_argument("-s", "--stock", action="append", required=True)
    parser.add_argument("--market", default="cn")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2] / ".cagent")
    parser.add_argument("-o", "--output", type=Path, default=Path("output/report/comparison.md"))
    args = parser.parse_args()
    agent = Agent(trace=True)
    companies = {stock: company_metrics(agent, args.root, args.market, stock) for stock in args.stock}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(args.output, render(companies).encode("utf-8"))
    print(args.output)
