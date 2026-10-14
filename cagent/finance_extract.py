"""finance_extract 模块：财报数字知识层

从年报 md 确定性定位并解析合并财务报表（资产负债表 / 利润表 / 现金流量表 / 股东权益变动表 /
现金流量表补充资料）的 HTML 表格；行项目 → 标准字段的映射交给模型（每份年报每类表映射一次并
缓存），产物 knowledge/<公告>/financials.json。数字一律程序直解，模型只负责定位与标注，不得改数。

用法：
    uv run cagent/finance_extract.py --stock 601633 --market cn
    uv run cagent/finance_extract.py --stock 601633 --market cn --force
"""

import argparse
import json
import logging
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import TypedDict

from cagent.agent import Agent
from cagent.chunk import report_year, split_pages
from cagent.scraper.lakehouse_index import atomic_write

logger = logging.getLogger(__name__)

_TABLE_RE = re.compile(r"<table.*?</table>", re.DOTALL)

# 报表标题行：合并*表（含 - 续）开启收集，公司/母公司*表或其他标题行结束收集
_HEADING_RE = re.compile(r"^(合并(?:及公司)?|公司|母公司)?(资产负债表|利润表|现金流量表|(?:股东|所有者)权益变动表)(?:\s*-\s*续)?$")

_STMT_KEY = {
    "资产负债表": "balance",
    "利润表": "income",
    "现金流量表": "cashflow",
    "股东权益变动表": "equity",
    "所有者权益变动表": "equity",
}


class RawTable(TypedDict):
    page: int
    rows: list[list[str]]


def _parse_rows(html: str) -> list[list]:
    """HTML 表格 → 行列表 [[文本, rowspan, colspan], ...]，未展开合并单元格。"""

    class _Parser(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.rows: list[list[list]] = []  # cell = [text_parts, rowspan, colspan]
            self._row: list[list] | None = None
            self._cell: list[str] | None = None

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if tag == "tr":
                self._row = []
            elif tag in ("td", "th") and self._row is not None:
                a = dict(attrs)
                self._row.append([[], int(a.get("rowspan") or 1), int(a.get("colspan") or 1)])
                self._cell = self._row[-1][0]

        def handle_data(self, data: str) -> None:
            if self._cell is not None:
                self._cell.append(data)

        def handle_endtag(self, tag: str) -> None:
            if tag in ("td", "th") and self._cell is not None:
                self._cell = None
            elif tag == "tr" and self._row is not None:
                self.rows.append(self._row)
                self._row = None

    p = _Parser()
    p.feed(html)
    return [[["".join(c[0]).strip(), c[1], c[2]] for c in row] for row in p.rows]


def _expand(rows: list[list]) -> list[list[str]]:
    """展开 rowspan/colspan 为规则网格。"""
    grid: list[list[str]] = []
    active: dict[int, list] = {}  # col -> [剩余行数, 文本]
    for row in rows:
        out: list[str] = []
        col = 0
        for text, rowspan, colspan in row:
            while active.get(col, [0])[0] > 0:
                out.append(active[col][1])
                active[col][0] -= 1
                col += 1
            for k in range(colspan):
                out.append(text)
                if rowspan > 1:
                    active[col + k] = [rowspan - 1, text]
            col += colspan
        grid.append(out)
    return grid


_NUM_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+")


def _norm_num(s: str) -> str:
    """千分位去逗号；(x) 负数 → -x；'-'/空 → 空串。其余文本原样返回。"""
    s = s.strip()
    if not s or s == "-":
        return ""
    neg = s.startswith("(") and s.endswith(")")
    core = s[1:-1] if neg else s
    if _NUM_RE.fullmatch(core):
        v = core.replace(",", "")
        return ("-" + v) if neg else v
    return s


def locate_statement_tables(text: str) -> dict[str, list[RawTable]]:
    """在年报 md 中定位合并报表表格，返回 {statement: [{page, rows}, ...]}（rows 已归一化数值）。

    按页扫描标题行：合并资产负债表/利润表/现金流量表/权益变动表（含 - 续）开启收集，
    遇到公司/母公司报表或其他标题行结束；现金流量表补充资料单独取附注中第一个。
    """
    pages = split_pages(text)
    found: dict[str, list[RawTable]] = {}
    current: str | None = None
    for page_no, page in enumerate(pages, 1):
        for line in page.splitlines():
            m = _HEADING_RE.match(line.strip())
            if m:
                current = _STMT_KEY[m.group(2)] if m.group(1) and m.group(1).startswith("合并") else None
        for tm in _TABLE_RE.finditer(page):
            if current:
                rows = [[_norm_num(c) for c in r] for r in _expand(_parse_rows(tm.group(0)))]
                found.setdefault(current, []).append({"page": page_no, "rows": rows})
    for page_no, page in enumerate(pages, 1):
        idx = page.find("现金流量表补充资料")
        if idx == -1:
            continue
        tm = _TABLE_RE.search(page, idx)
        if tm:
            rows = [[_norm_num(c) for c in r] for r in _expand(_parse_rows(tm.group(0)))]
            found["supplement"] = [{"page": page_no, "rows": rows}]
            break
    return found


# 标准字段：字段名 → 中文释义，交给模型按释义从原始行里找对应行项目

_INCOME_FIELDS = {
    "revenue": "营业总收入（或营业收入）",
    "cogs": "营业成本",
    "taxes_surcharges": "税金及附加",
    "selling_exp": "销售费用",
    "admin_exp": "管理费用",
    "rd_exp": "研发费用",
    "fin_exp": "财务费用（负数表示净收益）",
    "interest_exp": "其中：利息费用",
    "other_income": "其他收益",
    "invest_income": "投资收益",
    "fv_change_income": "公允价值变动收益",
    "credit_impairment_loss": "信用减值损失（按表格原符号，负数=损失）",
    "asset_impairment_loss": "资产减值损失（按表格原符号，负数=损失）",
    "asset_disposal_income": "资产处置收益",
    "operating_profit": "营业利润",
    "total_profit": "利润总额",
    "income_tax": "所得税费用",
    "net_profit": "净利润",
    "net_profit_parent": "归属于母公司股东的净利润",
    "minority_profit": "少数股东损益",
}

_BALANCE_FIELDS = {
    "total_assets": "资产总计",
    "total_liabilities": "负债合计",
    "total_equity": "股东权益合计",
    "equity_parent": "归属于母公司股东权益合计",
    "minority_equity": "少数股东权益",
    "monetary_funds": "货币资金",
    "trading_fin_assets": "交易性金融资产",
    "notes_receivable": "应收票据",
    "accounts_receivable": "应收账款",
    "receivables_financing": "应收款项融资",
    "prepayments": "预付款项",
    "other_receivables": "其他应收款",
    "inventory": "存货",
    "contract_assets": "合同资产",
    "fixed_assets": "固定资产",
    "construction_in_progress": "在建工程",
    "right_of_use_assets": "使用权资产",
    "intangible_assets": "无形资产",
    "development_exp": "开发支出",
    "goodwill": "商誉",
    "long_term_equity_invest": "长期股权投资",
    "short_term_borrowings": "短期借款",
    "long_term_borrowings": "长期借款",
    "bonds_payable": "应付债券",
    "lease_liabilities": "租赁负债",
    "notes_payable": "应付票据",
    "accounts_payable": "应付账款",
    "contract_liabilities": "合同负债",
    "payroll_payable": "应付职工薪酬",
    "tax_payable": "应交税费",
    "other_payables": "其他应付款",
    "share_capital": "股本",
    "treasury_stock": "减:库存股",
    "retained_earnings": "未分配利润",
}

_CASHFLOW_FIELDS = {
    "ocf": "经营活动产生的现金流量净额",
    "icf": "投资活动产生的现金流量净额（负数=净流出）",
    "financing_cf": "筹资活动产生的现金流量净额（负数=净流出）",
    "capex": "购建固定资产、无形资产和其他长期资产支付的现金",
    "cash_from_sales": "销售商品、提供劳务收到的现金",
    "dividend_interest_paid": "分配股利、利润或偿付利息支付的现金",
    "cash_end_balance": "年末现金及现金等价物余额",
}

_SUPPLEMENT_FIELDS = {
    "da_fixed": "固定资产折旧（本期数）",
    "da_rou": "使用权资产折旧（本期数）",
    "da_intangible": "无形资产摊销（本期数）",
    "da_lt_deferred": "长期待摊费用摊销（本期数）",
    "da_inv_property": "投资性房地产折旧及摊销（本期数，表中没有该行则填 null）",
}

_EQUITY_FIELDS = {
    "dividend": "对股东的分配：取利润分配部分『3.对股东的分配』行、未分配利润列的绝对值；只取第一期间（本年度）表，忽略上年期间表",
}

_FIELDS_BY_STMT = {
    "income": _INCOME_FIELDS,
    "balance": _BALANCE_FIELDS,
    "cashflow": _CASHFLOW_FIELDS,
    "supplement": _SUPPLEMENT_FIELDS,
    "equity": _EQUITY_FIELDS,
}

_PERIOD_HINT = {
    "income": "只取本期数（第一数据列，即本年度列）",
    "balance": "只取本期数（第一数据列，即期末列；附注列不是数据列）",
    "cashflow": "只取本期数（第一数据列，即本年度列）",
    "supplement": "只取本期数（第一数据列，即本年度列）",
    "equity": "只取第一期间（本年度）表；dividend 取利润分配部分『对股东的分配』行、未分配利润列的绝对值，忽略上年期间表",
}

_STMT_TITLE = {
    "income": "利润表",
    "balance": "资产负债表",
    "cashflow": "现金流量表",
    "supplement": "现金流量表补充资料",
    "equity": "股东权益变动表",
}


def _map_schema(fields: dict[str, str]) -> dict:
    return {
        "type": "object",
        "properties": {k: {"type": ["number", "null"]} for k in fields},
        "required": list(fields),
    }


def map_statement(agent: Agent, stmt: str, tables: list[RawTable]) -> dict:
    """让模型把一类报表的原始行映射到标准字段。只映射不改数，缺失填 null。"""
    fields = _FIELDS_BY_STMT[stmt]
    if not tables:
        return {k: None for k in fields}
    listing = "\n".join(f'<table page="{t["page"]}">\n{json.dumps(t["rows"], ensure_ascii=False)}\n</table>' for t in tables)
    prompt = f"""
<requirement>
你在做上市公司年报的财务报表科目映射。下面是合并{_STMT_TITLE[stmt]}的原始表格行（JSON，单元格数值已归一化：无千分位逗号，负数为前缀负号，空串=无）。
把行项目映射到 <fields> 的标准字段，{_PERIOD_HINT[stmt]}。
缺失填 null。只抄录原表的数，禁止计算、禁止估算、禁止改数。单位：人民币元。
</requirement>

<fields>
{json.dumps(fields, ensure_ascii=False, indent=1)}
</fields>

{listing}
"""
    result = agent.chat_json(prompt, _map_schema(fields))
    return {k: result.get(k) for k in fields}


def extract_financials(agent: Agent, md_path: str | Path, root: Path) -> dict:
    """一份年报 md → knowledge/<公告>/financials.json，返回解析后的 dict。"""
    md_path = Path(md_path)
    found = locate_statement_tables(md_path.read_text(encoding="utf-8"))
    for required in ("balance", "income", "cashflow"):
        if required not in found:
            raise ValueError(f"{md_path.name} 未定位到 {required} 报表表格")

    year = report_year(agent, md_path)
    financials: dict = {"year": year, "source": {"file": md_path.name, "pages": {}}}
    for stmt in ("income", "balance", "cashflow", "supplement", "equity"):
        tables = found.get(stmt, [])
        if tables:
            financials["source"]["pages"][stmt] = [t["page"] for t in tables]
        financials[stmt] = map_statement(agent, stmt, tables)
        logger.info(f"{md_path.name} {stmt}: {sum(1 for v in financials[stmt].values() if v is not None)}/{len(financials[stmt])} 个字段")

    out_dir = Path(root) / "knowledge" / md_path.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    atomic_write(out_dir / "financials.json", (json.dumps(financials, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
    logger.info(f"输出: {out_dir / 'financials.json'}")
    return financials


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S", stream=sys.stderr)
    parser = argparse.ArgumentParser(description="finance_extract：财报数字知识层")
    parser.add_argument("--stock", default="601633")
    parser.add_argument("--market", default="cn", choices=["cn", "hk"])
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1] / ".cagent"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    derived = Path(args.root) / args.market / args.stock / "derived"
    mds = sorted(derived.glob("*.md"))
    if not mds:
        raise SystemExit(f"{derived} 没有年报 md，先跑 ocr/cli.py")

    agent = Agent(trace=True)
    failed = []
    for md in mds:
        out = Path(args.root) / args.market / args.stock / "knowledge" / md.stem / "financials.json"
        if out.exists() and not args.force:
            logger.info(f"跳过（已存在）: {md.name}")
            continue
        try:
            extract_financials(agent, md, Path(args.root) / args.market / args.stock)
        except Exception:
            logger.exception(f"提取失败: {md.name}")
            failed.append(md.name)
    if failed:
        logger.error(f"失败 {len(failed)} 份: {failed}")
        sys.exit(1)


if __name__ == "__main__":
    main()
