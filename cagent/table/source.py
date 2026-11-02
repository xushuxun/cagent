"""按 LLM 给出的原文坐标直读表格单元格，核验提取数值。"""

import re
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser


class _Tables(HTMLParser):
    """保留 table/row/cell 顺序，供 r行c列坐标回读。"""

    def __init__(self):
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._table = -1
        self._row = -1
        self._cell = -1

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.tables.append([])
            self._table += 1
            self._row = -1
        elif tag == "tr" and self._table >= 0:
            self.tables[self._table].append([])
            self._row += 1
            self._cell = -1
        elif tag in ("td", "th") and self._row >= 0:
            self.tables[self._table][self._row].append("")
            self._cell += 1

    def handle_endtag(self, tag):
        if tag == "table":
            self._row = -1
        elif tag == "tr":
            self._cell = -1

    def handle_data(self, data):
        if self._table >= 0 and self._row >= 0 and self._cell >= 0:
            self.tables[self._table][self._row][self._cell] += data


def _cell(tables: list[list[list[str]]], row: dict) -> str | None:
    try:
        return tables[row["table"]][row["row"]][row["column"]]
    except (IndexError, TypeError):
        return None


def _amount(text: str) -> Decimal | None:
    text = text.strip().replace(",", "").replace("，", "")
    if text in ("", "-", "—", "--"):
        return None
    negative = text.startswith("(") and text.endswith(")")
    try:
        value = Decimal(text.strip("()"))
    except InvalidOperation:
        return None
    return -value if negative else value


def verify_rows(rows: list[dict], pages: list[str], start: int) -> list[dict]:
    """逐行回读原单元格；只报告坐标或数值不符，不修改提取结果。"""
    tables_by_page = {start + offset: parse_tables(text) for offset, text in enumerate(pages)}
    issues = []
    for index, row in enumerate(rows):
        cell = _cell(tables_by_page.get(row["page"], []), row)
        if cell is None:
            issues.append({"index": index, "reason": "坐标不存在", "row": row})
            continue
        expected = _amount(cell)
        actual = row["value"]
        if expected is not None and (actual is None or Decimal(str(actual)) != expected):
            issues.append({"index": index, "reason": "单元格数值不符", "expected": str(expected), "actual": actual, "row": row})
    return issues


def parse_tables(text: str) -> list[list[list[str]]]:
    parser = _Tables()
    parser.feed(text)
    return parser.tables


def grid_pages(pages: list[str], start: int) -> str:
    """把表格展开成带物理页号和单元格坐标的文本，供模型选择。

    例如第 142 页的 <table><tr><td>存货</td><td>1,200</td></tr></table> 会展开为：
    <page index="142"><table index="0">
    r0c0=存货 | r0c1=1,200
    </table></page>
    """
    parts = []
    for offset, text in enumerate(pages):
        parts.append(f'<page index="{start + offset}">')
        for table_index, table in enumerate(parse_tables(text)):
            parts.append(f'<table index="{table_index}">')
            for row_index, row in enumerate(table):
                parts.append(" | ".join(f"r{row_index}c{column_index}={cell.strip()}" for column_index, cell in enumerate(row)))
            parts.append("</table>")
        parts.append("</page>")
    return "\n".join(parts)


def materialize_rows(selections: list[dict], pages: list[str], start: int, current_columns: dict[tuple[int, int], int] | None = None) -> list[dict]:
    """按模型选择直读数值；current_columns 仅用于首次提取时限定当期列。

    例如模型选择 {"item": "存货", "page": 142, "table": 0, "value_cell": "r0c1"}，
    对应原文为 1,200，则输出 {"item": "存货", "value": 1200, "raw_value": "1,200",
    "page": 142, "table": 0, "row": 0, "column": 1}。
    若 current_columns={(142, 0): 2}，上述 r0c1 会因不在当期列而被拒绝。
    """
    tables = {start + offset: parse_tables(text) for offset, text in enumerate(pages)}
    rows = []
    for selection in selections:
        ref = selection["value_cell"] or selection["item_cell"]
        match = re.fullmatch(r"r(\d+)c(\d+)", ref)
        if not match:
            raise ValueError(f"无效单元格坐标: {selection}")
        if current_columns is not None and selection["value_cell"]:
            expected = current_columns.get((selection["page"], selection["table"]))
            if expected != int(match.group(2)):
                raise ValueError(f"金额不在唯一当期列: {selection}")
        source = {"page": selection["page"], "table": selection["table"], "row": int(match.group(1)), "column": int(match.group(2))}
        raw_value = _cell(tables.get(source["page"], []), source)
        if raw_value is None:
            raise ValueError(f"单元格不存在: {selection}")
        value = _amount(raw_value) if selection["value_cell"] else None
        rows.append(
            {
                "item": selection["item"],
                "value": int(value) if value is not None and value == value.to_integral() else float(value) if value is not None else None,
                "raw_value": raw_value if selection["value_cell"] else "",
                **source,
            }
        )
    return rows
