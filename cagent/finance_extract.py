"""finance_extract 模块：财报数字层

模型提表、程序校验。流程：LLM 选章节 → 程序出紧凑表清单 → LLM 挑合并报表 → LLM 逐表提数并标注 role →
程序跑会计恒等式（finance_identity）→ 不通过则把失败等式连原表回喂定向重取（≤2 轮）。
数字一律取原表值，模型禁止计算、禁止改数；恒等式不通过的数字进 unresolved，下游不得当作已核实。

产物：<root>/<market>/<stock>/knowledge/<公告stem>/financials.json（幂等缓存），字段：
    year/source/file/pages/picked  溯源与断点
    tables{kind: {title, consolidated, unit, pages, columns, rows[{label, role, values}]}}  原表行，values 按 columns 对齐
    roles / roles_prior            程序从 rows 归集出的本期、上期口径 {role: 元}
    complete/missing_kinds           产物是否完整（--kind 单抽会留下残缺年份，序列层要认这个标记）
    identities[{id, desc, status, delta, missing}]  恒等式结论（pass/fail/skip/diff）
    unresolved                     fail 的恒等式 id，这些数字下游不得当作已核实
只产内容，不管 UI；分析层见 wiki_finance.py。

用法：
    uv run cagent/finance_extract.py --stock 601633 --market cn
    uv run cagent/finance_extract.py --stock 601633 --market cn --force
    uv run cagent/finance_extract.py --stock 601633 --market cn --kind income    # 只重取一张表，其余沿用
"""

import argparse
import json
import logging
import re
import sys
import textwrap
from pathlib import Path

from cagent.agent import Agent
from cagent.chunk import Toc, report_year, split_pages
from cagent.finance_identity import IDENTITIES, ROLES_BY_KIND, Check, check_report, duplicate_roles, roles_from_rows, rollforward_check, verify
from cagent.scraper.lakehouse_index import atomic_write

logger = logging.getLogger(__name__)

_TABLE_RE = re.compile(r"<table.*?</table>", re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")

KINDS = ("balance", "income", "cashflow", "supplement", "equity")

_IDENTITY_BY_ID = {identity.id: identity for identity in IDENTITIES}

_KIND_TITLE = {
    "balance": "合并资产负债表",
    "income": "合并利润表",
    "cashflow": "合并现金流量表",
    "supplement": "现金流量表补充资料（将净利润调节为经营活动现金流量）",
    "equity": "合并股东权益变动表",
}


def _cell_text(html: str) -> str:
    return _TAG_RE.sub("", html).replace("&nbsp;", " ").replace("&amp;", "&").strip()


def _table_title(page_text: str, position: int) -> str:
    """表格上方最近的几行文本——年报里这就是「合并资产负债表」这类表名。"""
    lines = [line.strip() for line in page_text[:position].splitlines() if line.strip()]
    return " / ".join(lines[-3:])[:60]


def _rows_of(html: str) -> list[list[str]]:
    """HTML 表格 → 单元格文本网格（不展开合并单元格，交给模型看原文）。"""
    return [[_cell_text(cell) for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.DOTALL)] for row in re.findall(r"<tr.*?</tr>", html, re.DOTALL)]


def table_inventory(pages: list[str], page_range: range) -> list[dict]:
    """候选表清单：页码、表序号、行数、表名、前几个行项目。程序只做切片，判断交给模型。"""
    inventory = []
    for page_no in page_range:
        page_text = pages[page_no - 1]
        for index, table in enumerate(_TABLE_RE.finditer(page_text), 1):
            grid = [row for row in _rows_of(table.group(0)) if row]
            if not grid:
                continue
            inventory.append(
                {
                    "id": f"{page_no}:{index}",
                    "page": page_no,
                    "index": index,
                    "rows": len(grid),
                    "title": _table_title(page_text, table.start()),
                    "labels": [row[0] for row in grid[1:6]],
                    "html": table.group(0),
                }
            )
    return inventory


def _inventory_text(inventory: list[dict]) -> str:
    return "\n".join(
        f'<table id="{item["id"]}" rows="{item["rows"]}" title="{item["title"]}">{"|".join(item["labels"])}</table>' for item in inventory
    )


def pick_tables(agent: Agent, inventory: list[dict]) -> dict[str, list[str]]:
    """让模型从清单里挑出各张合并报表（排除母公司口径），返回 {kind: [table id]}。"""
    listing = "\n".join(f"- {kind}：{_KIND_TITLE[kind]}" for kind in KINDS)
    prompt = textwrap.dedent(f"""
        <requirement>
        下面是某上市公司年报里若干表格的清单（id=页码:表序号，title=表格上方最近的文本，内容是前几个行项目）。
        为下列每类合并报表挑出对应的表 id：

        {listing}

        只选合并口径，绝不选母公司（公司）口径；同一张表跨页时把各页的表都选上；
        宁缺毋滥：表名或标题里带「公司」「母公司」的一律不选，附注里的明细表（账龄、减值、担保等）也不选；
        找不到的类别返回空数组。只输出 id，不要解释。
        </requirement>

        <tables>
        {_inventory_text(inventory)}
        </tables>""").strip()
    return agent.chat_json(prompt, {"type": "object", "properties": {kind: {"type": "array", "items": {"type": "string"}} for kind in KINDS}, "required": list(KINDS)})


def _roles_text(kind: str) -> str:
    return "\n".join(f"- {role}：{desc}" for role, desc in ROLES_BY_KIND[kind].items())


def _extract_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "consolidated": {"type": "boolean"},
            "title": {"type": "string"},
            "unit": {"type": "string"},
            "pages": {"type": "array", "items": {"type": "integer"}},
            "columns": {"type": "array", "items": {"type": "object", "properties": {"label": {"type": "string"}, "period": {"type": "string"}}, "required": ["label", "period"]}},
            "rows": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "role": {"type": "string"},
                        "values": {"type": "array", "items": {"type": ["number", "null"]}},
                    },
                    "required": ["label", "role", "values"],
                },
            },
        },
        "required": ["consolidated", "title", "unit", "pages", "columns", "rows"],
    }


def _equity_prompt(tables_html: str) -> str:
    return textwrap.dedent(f"""
        <requirement>
        你在读合并股东权益变动表。这张表的列是权益构成（股本、资本公积、未分配利润、股东权益合计……），
        不是期间；两期数据是上下相接的两张表（本年金额一张、上年金额一张）。

        只提取两笔业务事项，每行 values 两个数：第一个是本期（本年金额表）、第二个是上期（上年金额表）：
        - dividend：利润分配下「对股东的分配」行，取**未分配利润列**的绝对值（不是股东权益合计列）
        - capital_raised：「所有者投入和减少资本」行，取归母权益小计列；表中没有小计列时取股东权益合计列

        - 抄录原表数值，禁止计算、禁止改数；千分位逗号去掉，括号负数改成负号
        - label 照抄原表行标签，unit 照抄原表单位；找不到的行 values 填 [null, null]
        - columns 只放本期、上期两项；rows 只放上面这两行
        </requirement>

        <tables>
        {tables_html}
        </tables>""").strip()


def _extract_prompt(kind: str, tables_html: str) -> str:
    return textwrap.dedent(f"""
        <requirement>
        你在把上市公司年报的{_KIND_TITLE[kind]}抄成结构化数据。原表在 <tables> 里，跨页的续表已按顺序拼好。

        - 若 <tables> 里混进了母公司（公司）报表或其他无关表，只抄合并口径那张，其余忽略
        - consolidated 填 true 仅当你抄的是合并报表；母公司（公司）报表填 false

        - 抄录原表数值，禁止计算、禁止改数、禁止四舍五入；原表保留几位就抄几位
        - 千分位逗号去掉，括号负数改成负号（如 (1,234.5) → -1234.5）
        - 只把数据列放进 columns：附注列、序号列、单位列都不是数据列；每行 values 按 columns 顺序对齐
        - 本期列在前、上期列在后，列名照抄原表（含「经审计」「已重述」等字样）
        - 原表单元格是「-」「—」或空白时：该科目本期确实为零（可由合计行勾稽印证，如少数股东损益、少数股东权益）填 0；
          原表根本没这一行、或该数不适用则填 null
        - 每行都抄，保持原表顺序；<page> 上标了原表行数 rows=N，拼接后的 rows 条数应与各表行数之和相当，
          分类标题行可略过，数据行一行都不能漏
        - role 只从下面的词表里选，抄不到对应科目的行 role 填空字符串
        - 词表里注明「每行都标此 role」的除外，其余 role 每行只标一次：合计行与其下「其中:xxx」明细行不要标同一个 role
        - unit 填原表单位（元/千元/百万元），照抄原表
        </requirement>

        <roles>
        {_roles_text(kind)}
        </roles>

        <tables>
        {tables_html}
        </tables>""").strip()


def _repair_prompt(kind: str, tables_html: str, table: dict, failures: list[Check], roles: dict[str, float]) -> str:
    lines = []
    for check in failures:
        terms = " ".join(f"{sign:+.0f}×{role}={roles.get(role, 0.0):,.2f}" for role, sign in _IDENTITY_BY_ID[check.id].terms)
        lines.append(f"- {check.id}（{check.desc}）当前 Δ={check.delta:,.2f}\n  {terms}")
    return textwrap.dedent(f"""
        <requirement>
        你把年报的{_KIND_TITLE[kind]}抄进 <current>，但会计恒等式校验不通过：

        {"\n".join(lines)}

        请对照 <tables> 里的原表逐行核对，纠正错抄、漏抄、串列的数，重新输出整张表。
        仍然禁止计算：所有数字必须能在原表里逐字找到。
        </requirement>

        <roles>
        {_roles_text(kind)}
        </roles>

        <current>
        {json.dumps(table, ensure_ascii=False)}
        </current>

        <tables>
        {tables_html}
        </tables>""").strip()


def _consolidated_check(kind: str, table: dict) -> Check:
    """口径守卫：母公司报表自身也满足全部恒等式，抄错了恒等式抓不住，只能靠口径自报挡一道。"""
    ok = table.get("consolidated") is True
    return Check(f"scope.{kind}", "抄的是合并报表口径（母公司报表同样平，靠这一条挡）", "pass" if ok else "fail")


def _tables_html(inventory: list[dict], ids: list[str]) -> str:
    parts = []
    for table_id in ids:
        item = next((i for i in inventory if i["id"] == table_id), None)
        if item is None:
            logger.warning(f"清单里没有表 {table_id}，跳过")
            continue
        parts.append(f'<page no="{item["page"]}" title="{item["title"]}" rows="{item["rows"]}">\n{item["html"]}\n</page>')
    return "\n\n".join(parts)


def extract_kind(agent: Agent, kind: str, inventory: list[dict], ids: list[str], max_repair: int = 2) -> tuple[dict | None, list[Check]]:
    """抽一张表并用恒等式反复校验，返回 (表, 校验结果)。

    权益变动表只提两笔事实、且要用到利润表与资产负债表的数才能校验，故不走这张表自己的重取循环，
    残差在跨表校验里报。
    """
    html = _tables_html(inventory, ids)
    if not html:
        return None, []
    prompt = _equity_prompt(html) if kind == "equity" else _extract_prompt(kind, html)
    table = agent.chat_json(prompt, _extract_schema())
    if kind == "equity":
        return table, []
    for attempt in range(max_repair + 1):
        roles = roles_from_rows(table.get("rows") or [], unit=table.get("unit") or "元")
        checks = [check for check in verify(roles) if check.id.startswith(kind)]
        failures = [check for check in checks if check.status == "fail"]
        if not failures:
            return table, checks
        if attempt == max_repair:
            logger.warning(f"{kind} 仍有 {len(failures)} 条恒等式不通过：{[c.id for c in failures]}")
            return table, checks
        logger.info(f"{kind} 第 {attempt + 1} 次重取：{[c.id for c in failures]}")
        table = agent.chat_json(_repair_prompt(kind, html, table, failures, roles), _extract_schema())
    return table, checks


def extract_financials(agent: Agent, md_path: str | Path, root: Path, market: str = "", stock: str = "", kinds: tuple[str, ...] = KINDS, base: dict | None = None) -> dict:
    """一份年报 md → financials.json，返回解析后的 dict。

    kinds 限定只重取哪几张表（迭代单张表用）；base 是已有产物，未重取的表与恒等式结论沿用。
    """
    md_path = Path(md_path)
    toc = Toc(agent, md_path)
    pages = split_pages(toc.text)

    task = "找出合并财务报表（合并资产负债表、合并利润表、合并现金流量表、所有者权益变动表）以及财务报表附注所在章节"
    picked = toc.pick_chapters(toc.chapters, task)
    page_range = range(min(toc.range(toc.chapters[i])[0] for i in picked), max(toc.range(toc.chapters[i])[1] for i in picked) + 1)
    logger.info(f"财务报告章节: p.{page_range.start}-{page_range.stop - 1}（选中 {[toc.chapters[i]['title'] for i in picked]}）")

    inventory = table_inventory(pages, page_range)
    logger.info(f"候选表 {len(inventory)} 张，清单 {len(_inventory_text(inventory))} 字符")
    picked_tables = (base or {}).get("source", {}).get("picked") or pick_tables(agent, inventory)
    logger.info(f"选中: {{{', '.join(f'{k}: {v}' for k, v in picked_tables.items())}}}")

    tables: dict[str, dict] = {kind: table for kind, table in (base or {}).get("tables", {}).items() if kind not in kinds}
    all_checks: list[Check] = [Check(**check) for check in (base or {}).get("identities", []) if check["id"].split(".")[0] not in kinds and not check["id"].startswith(("cross", "equity"))]
    for kind in kinds:
        ids = [i for i in picked_tables.get(kind) or [] if isinstance(i, str)]
        if not ids:
            logger.warning(f"{kind}: 未选中任何表")
            continue
        table, checks = extract_kind(agent, kind, inventory, ids)
        if table is None:
            continue
        tables[kind] = table
        checks = [*checks, _consolidated_check(kind, table)]
        all_checks.extend(checks)
        rows = table.get("rows") or []
        roles = roles_from_rows(rows, unit=table.get("unit") or "元")
        logger.info(f"{kind}: {len(rows)} 行, {len(roles)} 个 role\n{check_report(checks)}")
        for role, labels in duplicate_roles(rows).items():
            logger.warning(f"{kind}: role {role} 被多行标注 {labels}，只取第一行")

    roles: dict[str, float] = {}
    roles_prior: dict[str, float] = {}
    for table in tables.values():
        rows = table.get("rows") or []
        unit = table.get("unit") or "元"
        roles.update(roles_from_rows(rows, unit=unit))
        roles_prior.update(roles_from_rows(rows, unit=unit, column=1))
    cross_checks = [check for check in verify(roles) if check.id.startswith("cross")]
    cross_checks.append(rollforward_check(roles, roles_prior))
    all_checks.extend(cross_checks)

    unresolved = [check.id for check in all_checks if check.status == "fail"]
    missing_kinds = [kind for kind in KINDS if kind not in tables]
    financials = {
        "year": report_year(agent, md_path),
        "source": {
            "market": market,
            "stock": stock,
            "file": md_path.name,
            "pages": {kind: table.get("pages") for kind, table in tables.items()},
            "picked": picked_tables,
        },
        "unit": next((table.get("unit") for table in tables.values() if table.get("unit")), "元"),
        "complete": not missing_kinds,
        "missing_kinds": missing_kinds,
        "tables": tables,
        "roles": roles,
        "roles_prior": roles_prior,
        "identities": [check.__dict__ for check in all_checks],
        "unresolved": unresolved,
    }
    logger.info(f"跨表校验:\n{check_report(cross_checks)}")
    return financials


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S", stream=sys.stderr)
    parser = argparse.ArgumentParser(description="finance_extract：财报数字层")
    parser.add_argument("--stock", default="601633")
    parser.add_argument("--market", default="cn", choices=["cn", "hk"])
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1] / ".cagent"))
    parser.add_argument("--force", action="store_true", help="重取已存在的 financials.json")
    parser.add_argument("--kind", action="append", choices=KINDS, help="只重取某几张表（可重复），其余沿用已有产物")
    args = parser.parse_args()

    derived = Path(args.root) / args.market / args.stock / "derived"
    mds = sorted(derived.glob("*.md"))
    if not mds:
        raise SystemExit(f"{derived} 没有年报 md，先跑 ocr/cli.py")

    kinds = tuple(args.kind) if args.kind else KINDS
    agent = Agent(trace=True)
    failed: list[str] = []
    for md_path in mds:
        out_path = Path(args.root) / args.market / args.stock / "knowledge" / md_path.stem / "financials.json"
        if out_path.exists() and not args.force and not args.kind:
            logger.info(f"跳过（已存在）: {out_path}")
            continue
        base = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else None
        if args.kind and base is None:
            logger.warning(f"{out_path} 不存在，只抽 {kinds} 会得到不完整的产物")
        try:
            financials = extract_financials(agent, md_path, Path(args.root), args.market, args.stock, kinds=kinds, base=base)
        except Exception:
            logger.exception(f"提取失败: {md_path.name}")
            failed.append(md_path.name)
            continue
        out_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(out_path, (json.dumps(financials, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
        logger.info(f"输出: {out_path}（{financials['year']} 年，未通过恒等式 {len(financials['unresolved'])} 条）")

    if failed:
        logger.error(f"失败 {len(failed)} 份: {failed}")
        sys.exit(1)


if __name__ == "__main__":
    main()
