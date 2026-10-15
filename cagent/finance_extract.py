"""finance_extract 模块：财报数字层

模型提表、程序校验。流程：LLM 选章节 → 程序出紧凑表清单 → LLM 挑合并报表 → LLM 逐表提数并标注 role →
程序跑会计恒等式（finance_identity）→ 不通过则把失败等式连原表回喂定向重取（≤2 轮）。
数字一律取原表值，模型禁止计算、禁止改数；恒等式不通过的数字进 unresolved，下游不得当作已核实。

产物：<root>/<market>/<stock>/knowledge/<公告stem>/financials.json（幂等缓存），字段：
    year/source/file/pages/picked  溯源与断点
    tables{kind: {title, consolidated, unit, pages, columns, rows[{label, role, values}]}}  原表行，values 按 columns 对齐
    roles / roles_prior            程序从 rows 归集出的本期、上期口径 {role: 元}
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
from cagent.finance_identity import IDENTITIES, ROLES_BY_KIND, Check, check_report, duplicate_roles, number, roles_from_rows, rollforward_check, verify
from cagent.scraper.lakehouse_index import atomic_write

logger = logging.getLogger(__name__)

_TABLE_RE = re.compile(r"<table.*?</table>", re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")

KINDS = ("balance", "income", "cashflow", "supplement", "equity")
NOTES = "notes"          # 附注事实：不在三表里，靠章节检索 + 段落级定位抽取
_ALL_KINDS = (*KINDS, NOTES)

_IDENTITY_BY_ID = {identity.id: identity for identity in IDENTITIES}

_KIND_TITLE = {
    "balance": "合并资产负债表",
    "income": "合并利润表",
    "cashflow": "合并现金流量表",
    "supplement": "现金流量表补充资料（将净利润调节为经营活动现金流量）",
    "equity": "合并股东权益变动表",
}

_NOTES_TASK = "找出披露这些事项的附注片段：应收账款账龄与坏账准备、存货跌价准备、货币资金受限部分"
# 检索锚点：这些事项在附注里的叫法是固定披露口径，用字面命中兜底，避免"模型没选中片段"被当成"年报没披露"
_NOTES_ANCHORS = ("账龄", "坏账准备", "信用损失", "跌价准备", "使用权受到限制", "受限")


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
    """输出用紧凑格式：decode 是瓶颈，行用四元数组 [行名, role, 本期, 上期]，比对象省四成 token。"""
    return {
        "type": "object",
        "properties": {
            "consolidated": {"type": "boolean"},
            "title": {"type": "string"},
            "unit": {"type": "string"},
            "pages": {"type": "array", "items": {"type": "integer"}},
            "columns": {"type": "array", "items": {"type": "string"}},
            "rows": {"type": "array", "items": {"type": "array", "items": {"type": ["string", "number", "null"]}}},
        },
        "required": ["consolidated", "title", "unit", "pages", "columns", "rows"],
    }


def _patch_schema() -> dict:
    """重取只回错的那些行，不回整张表。"""
    return {
        "type": "object",
        "properties": {"rows": {"type": "array", "items": {"type": "array", "items": {"type": ["string", "number", "null"]}}}},
        "required": ["rows"],
    }


def _rows_from_compact(raw_rows: list | None, columns: list[str]) -> list[dict]:
    """四元数组 → 规范行 dict；列数不足补 null，多则截断。"""
    rows = []
    for item in raw_rows or []:
        cells = list(item) if isinstance(item, (list, tuple)) else [item]
        cells += [None] * (4 - len(cells))
        label, role = cells[0], cells[1]
        # 模型有时把数写成字符串（"10045290706.33"）或留空串，统一成 float/None，下游要拿它做乘法
        values = [number(value) for value in [cells[2], cells[3]][: max(1, len(columns) or 2)]]
        rows.append({"label": str(label or "").strip(), "role": str(role or "").strip(), "values": values})
    return rows


def _equity_prompt(tables_html: str) -> str:
    return textwrap.dedent(f"""
        <requirement>
        你在读合并股东权益变动表。这张表的列是权益构成（股本、资本公积、未分配利润、股东权益合计……），
        不是期间；两期数据是上下相接的两张表（本年金额一张、上年金额一张）。

        只提取两笔业务事项，行用四元数组 [原表行名, role, 本期数, 上期数]（本期取本年金额表、上期取上年金额表）：
        - dividend：利润分配下「对股东的分配」行，取**未分配利润列**的绝对值（不是股东权益合计列）
        - capital_raised：「所有者投入和减少资本」行，取归母权益小计列；表中没有小计列时取股东权益合计列

        - 抄录原表数值，禁止计算、禁止改数；千分位逗号去掉，括号负数改成负号
        - unit 照抄原表单位；两项都找不到就不输出行
        - columns 填 [本期, 上期]；rows 只放上面这两行
        </requirement>

        <tables>
        {tables_html}
        </tables>""").strip()


def _statement_prefix(kind: str, tables_html: str) -> str:
    """提示词的共享前缀：重取时前缀一字不改，服务端 KV 可以直接复用，等于只花尾巴的 prefill。"""
    return textwrap.dedent(f"""
        <requirement>
        你在把上市公司年报的{_KIND_TITLE[kind]}抄成结构化数据。原表在 <tables> 里，跨页的续表已按顺序拼好。

        - 若 <tables> 里混进了母公司（公司）报表或其他无关表，只抄合并口径那张，其余忽略
        - consolidated 填 true 仅当你抄的是合并报表；母公司（公司）报表填 false
        - 抄录原表数值，禁止计算、禁止改数、禁止四舍五入；原表保留几位就抄几位
        - 千分位逗号去掉，括号负数改成负号（如 (1,234.5) → -1234.5）
        - columns 只放数据列名（附注列、序号列、单位列都不是数据列），最多两列：本期在前、上期在后
        - 行用四元数组：[原表行名, role, 本期数, 上期数]；上期没有就填 null
        - **只输出词表里有 role 的行**，其余明细行不要输出——词表外的行下游用不到，抄了只是白花时间
        - 原表单元格是「-」「—」或空白时：该科目确实为零（可由合计行勾稽印证，如少数股东损益）填 0；
          原表没有这一行、或该数不适用则填 null
        - unit 填原表单位（元/千元/百万元），照抄原表
        </requirement>

        <roles>
        {_roles_text(kind)}
        </roles>

        <tables>
        {tables_html}
        </tables>""").strip()


def _extract_prompt(kind: str, tables_html: str) -> str:
    return _statement_prefix(kind, tables_html) + "\n\n<task>按上面的四元数组格式输出全部 role 行。</task>"


def _repair_prompt(kind: str, tables_html: str, wrong_rows: list[dict], failures: list[Check], roles: dict[str, float]) -> str:
    lines = []
    for check in failures:
        terms = " ".join(f"{sign:+.0f}×{role}={roles.get(role, 0.0):,.2f}" for role, sign in _IDENTITY_BY_ID[check.id].terms)
        lines.append(f"- {check.id}（{check.desc}）当前 Δ={check.delta:,.2f}\n  {terms}")
    current = [[row.get("label"), row.get("role"), *[v for v in (row.get("values") or [None, None])[:2]]] for row in wrong_rows]
    return _statement_prefix(kind, tables_html) + textwrap.dedent(f"""

        <current>
        {json.dumps(current, ensure_ascii=False)}
        </current>

        <task>
        上面这几行的数让会计恒等式对不上：

        {"\n".join(lines)}

        请对照 <tables> 原表逐行核对（错抄、漏抄、串列、漏了括号负号），**只输出需要改正的那几行**，
        格式与上面相同；没把握的行也照原表重抄一遍。不要输出其它行。
        </task>""")


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
    table = _normalize_table(agent.chat_json(prompt, _extract_schema()))
    if kind == "equity":
        return table, []
    for attempt in range(max_repair + 1):
        roles = roles_from_rows(table["rows"], unit=table.get("unit") or "元")
        checks = [check for check in verify(roles) if check.id.startswith(kind)]
        failures = [check for check in checks if check.status == "fail"]
        if not failures:
            return table, checks
        if attempt == max_repair:
            logger.warning(f"{kind} 仍有 {len(failures)} 条恒等式不通过：{[c.id for c in failures]}")
            return table, checks
        # 只把与失败恒等式有关的行回给模型改，返回的也只是这几行
        suspects = {role for check in failures for role, _ in _IDENTITY_BY_ID[check.id].terms}
        wrong_rows = [row for row in table["rows"] if row.get("role") in suspects] or table["rows"]
        logger.info(f"{kind} 第 {attempt + 1} 次重取（{len(wrong_rows)} 行）：{[c.id for c in failures]}")
        patch = _rows_from_compact(agent.chat_json(_repair_prompt(kind, html, wrong_rows, failures, roles), _patch_schema()).get("rows"), table["columns"])
        candidate = {**table, "rows": _merged_rows(table["rows"], patch)}
        candidate_failures = _failure_count(kind, candidate)
        if candidate_failures >= len(failures):
            logger.warning(f"{kind} 重取没有变好（{len(failures)} → {candidate_failures}），保留原结果")
            return table, checks
        table = candidate
    return table, checks


def _number_forms(value: float) -> list[str]:
    """一个数在年报里可能的写法：不同小数位，以及千元/万元/亿元等单位的换算写法。

    年报里同一个数可能写作「1.12亿元」或「5,037,603 千元」，只按原样匹配会误报。
    """
    forms: set[str] = set()
    for scaled in (value, value / 1e3, value * 1e3, value / 1e4, value * 1e4, value / 1e8, value * 1e8):
        for text in (f"{scaled:,.2f}", f"{scaled:,.0f}", f"{scaled:f}".rstrip("0").rstrip("."), f"{scaled:,.6f}".rstrip("0").rstrip(".")):
            cleaned = text.replace(",", "")
            if cleaned and cleaned not in {"0", "-0"}:
                forms.add(cleaned)
    return sorted(forms)


def verbatim_check(kind: str, table: dict, report_text: str) -> Check:
    """数字必须在年报原文里逐字出现。

    恒等式能保勾稽，保不了"这个数是不是报表里的"——模型完全可以编一个让等式成立的数
    （实测出现过：FY2023 存货账面余额 26,820,920,280.72 在年报里一次都没有）。
    """
    squashed = report_text.replace(",", "").replace("，", "")
    missing = []
    for row in table.get("rows") or []:
        for value in row.get("values") or []:
            if value is None or abs(float(value)) < 1000:   # 太小的数在全文中易巧合命中，不作为证据
                continue
            if not any(form in squashed for form in _number_forms(float(value))):
                missing.append(f'{row.get("role") or row.get("label")}={float(value):,.2f}')
                break
    status = "pass" if not missing else "fail"
    return Check(f"verbatim.{kind}", "每个数都要在年报原文里逐字出现", status, None, tuple(missing[:5]))


def _failure_count(kind: str, table: dict, known_roles: dict[str, float] | None = None) -> int:
    """这张表当前有几条恒等式不成立——用来判断重取有没有变好。"""
    roles = {**(known_roles or {}), **roles_from_rows(table.get("rows") or [], unit=table.get("unit") or "元")}
    return sum(1 for check in verify(roles) if check.id.startswith(kind) and check.status == "fail")


def _normalize_table(raw: dict) -> dict:
    """模型返回的紧凑表 → 规范表（行变 dict，columns 变字符串列表）。"""
    columns = [str(c) for c in (raw.get("columns") or [])]
    return {**raw, "columns": columns, "rows": [row for row in _rows_from_compact(raw.get("rows"), columns) if row["role"]]}


def _merged_rows(rows: list[dict], patch: list[dict]) -> list[dict]:
    """把补丁行按 role 覆盖回原行；原表里没有的新 role 追加在后面。"""
    merged, seen = [], set()
    by_role = {row["role"]: row for row in patch if row.get("role")}
    for row in rows:
        replacement = by_role.get(row.get("role") or "")
        merged.append(replacement or row)
        seen.add(row.get("role"))
    merged.extend(row for role, row in by_role.items() if role not in seen)
    return merged


def extract_notes(agent: Agent, toc: Toc, known_roles: dict[str, float] | None = None, max_repair: int = 2) -> tuple[dict | None, list[Check]]:
    """附注事实：章节检索 → 片段定位 → 提数标 role → 用 notes 恒等式对账 → 不通过则定向重取。

    事实散在附注各处，不像三表那样能一次给全，所以先检索再抽取；页码随表带出。
    """
    known_roles = known_roles or {}
    try:
        picked = sorted(set(toc.pick_chapters(toc.chapters, _NOTES_TASK)))
    except ValueError:
        logger.warning("附注章节定位失败，本次只抽三表")
        return None, []
    chunks = [chunk for i in picked for chunk in toc.chapter_text_chunk(i, budget=6144)]
    choices = toc.pick_chunks(chunks, _NOTES_TASK, limit=8)
    anchored: list[int] = []
    for anchor in _NOTES_ANCHORS:
        hits = [index for index, chunk in enumerate(chunks) if anchor in chunk]
        anchored += [index for index in hits[:2] if index not in choices and index not in anchored]
    selected = [chunks[i] for i in choices] + [chunks[i] for i in anchored]
    logger.info(f"附注：章节 {[toc.chapters[i]['title'] for i in picked]} / 片段 {len(chunks)} → 模型选 {choices} + 锚点 {anchored}")
    if not selected:
        logger.warning("附注检索不到相关片段")
        return None, []
    html = "\n\n".join(selected[:16])

    prefix = textwrap.dedent(f"""
        <requirement>
        你在读年报附注，把下面这些事实抄成结构化数据。只抄原文写明的数，禁止计算、禁止估算、禁止改数。

        - 千分位逗号去掉，括号负数改成负号；原表单位不是元的，在 unit 里写明（如「千元」）
        - 行用四元数组：[披露名, role, 本期数, 上期数]；columns 填 [本期, 上期] 两个列名
        - 只输出词表里有的 role；原文没有披露的事实不要编、不要拿别的数顶替，缺哪项就不输出哪项
        - pages 填事实出现的页码（正文里 <!-- page N --> 标记的页号）
        </requirement>

        <roles>
        {_roles_text(NOTES)}
        </roles>

        <segments>
        {html}
        </segments>""").strip()

    table = _normalize_table(agent.chat_json(prefix + "\n\n<task>按四元数组输出上述事实。</task>", _extract_schema()))
    for attempt in range(max_repair + 1):
        roles = roles_from_rows(table["rows"], unit=table.get("unit") or "元")
        checks = [check for check in verify({**(known_roles or {}), **roles}) if check.id.startswith("notes")]
        failures = [check for check in checks if check.status == "fail"]
        if not failures:
            return table, checks
        if attempt == max_repair:
            logger.warning(f"附注仍有 {len(failures)} 条不成立：{[c.id for c in failures]}")
            return table, checks
        suspects = {role for check in failures for role, _ in _IDENTITY_BY_ID[check.id].terms}
        wrong_rows = [row for row in table["rows"] if row.get("role") in suspects] or table["rows"]
        lines = []
        for check in failures:
            terms = []
            for role, _ in _IDENTITY_BY_ID[check.id].terms:
                if role in known_roles:
                    terms.append(f"{role}={known_roles[role]:,.2f}（来自主表，已核实，不要改）")
                else:
                    terms.append(f"{role}={roles.get(role, float('nan')):,.2f}（你给的）")
            lines.append(f"- {check.id}（{check.desc}）当前 Δ={check.delta:,.2f}\n  " + "；".join(terms))
        detail = "\n".join(lines)
        logger.info(f"附注第 {attempt + 1} 次重取（{len(wrong_rows)} 行）：{[c.id for c in failures]}")
        current = [[row.get("label"), row.get("role"), *[v for v in (row.get("values") or [None, None])[:2]]] for row in wrong_rows]
        patch_prompt = prefix + textwrap.dedent(f"""

            <current>
            {json.dumps(current, ensure_ascii=False)}
            </current>

            <task>
            上面这几项与主表对不上：

            {detail}

            回 <segments> 核对：账龄表的账面余额是否含了坏账准备、跌价准备是否抄成了账面价值、
            受限资金是否抄成了全部货币资金、单位是否换算错。**只输出需要改正的那几行**（同样的四元数组）。
            </task>""")
        patch = _rows_from_compact(agent.chat_json(patch_prompt, _patch_schema()).get("rows"), table["columns"])
        candidate = {**table, "rows": _merged_rows(table["rows"], patch)}
        candidate_failures = _failure_count(NOTES, candidate, known_roles)
        if candidate_failures >= len(failures):
            logger.warning(f"附注重取没有变好（{len(failures)} → {candidate_failures}），保留原结果")
            return table, checks
        table = candidate
    return table, checks


def extract_financials(agent: Agent, md_path: str | Path, root: Path, market: str = "", stock: str = "", kinds: tuple[str, ...] = _ALL_KINDS, base: dict | None = None) -> dict:
    """一份年报 md → financials.json，返回解析后的 dict。

    kinds 限定只重取哪几张表（迭代单张表用）；base 是已有产物，未重取的表与恒等式结论沿用。
    """
    md_path = Path(md_path)
    toc = Toc(agent, md_path)
    pages = split_pages(toc.text)
    report_text = toc.text

    task = "找出合并财务报表（合并资产负债表、合并利润表、合并现金流量表、所有者权益变动表）以及财务报表附注所在章节"
    picked = toc.pick_chapters(toc.chapters, task)
    page_range = range(min(toc.range(toc.chapters[i])[0] for i in picked), max(toc.range(toc.chapters[i])[1] for i in picked) + 1)
    logger.info(f"财务报告章节: p.{page_range.start}-{page_range.stop - 1}（选中 {[toc.chapters[i]['title'] for i in picked]}）")

    inventory = table_inventory(pages, page_range)
    logger.info(f"候选表 {len(inventory)} 张，清单 {len(_inventory_text(inventory))} 字符")
    picked_tables = (base or {}).get("source", {}).get("picked") or pick_tables(agent, inventory)
    logger.info(f"选中: {{{', '.join(f'{k}: {v}' for k, v in picked_tables.items())}}}")

    tables: dict[str, dict] = {kind: table for kind, table in (base or {}).get("tables", {}).items() if kind not in kinds}
    # 沿用旧结论时，必须把这一轮重取的 kind 相关结论全部丢掉，否则上一轮的 fail 会一直挂着
    def check_kind(check_id: str) -> str | None:
        parts = check_id.split(".")
        return next((part for part in parts if part in _ALL_KINDS), None)

    all_checks: list[Check] = [
        Check(**check)
        for check in (base or {}).get("identities", [])
        if check_kind(check["id"]) not in kinds and not check["id"].startswith(("cross", "equity"))
    ]
    for kind in [k for k in kinds if k != NOTES]:
        ids = [i for i in picked_tables.get(kind) or [] if isinstance(i, str)]
        if not ids:
            logger.warning(f"{kind}: 未选中任何表")
            continue
        table, checks = extract_kind(agent, kind, inventory, ids)
        if table is None:
            continue
        tables[kind] = table
        checks = [*checks, _consolidated_check(kind, table), verbatim_check(kind, table, report_text)]
        all_checks.extend(checks)
        rows = table.get("rows") or []
        roles = roles_from_rows(rows, unit=table.get("unit") or "元")
        logger.info(f"{kind}: {len(rows)} 行, {len(roles)} 个 role\n{check_report(checks)}")
        for role, labels in duplicate_roles(rows).items():
            logger.warning(f"{kind}: role {role} 被多行标注 {labels}，只取第一行")

    if NOTES in kinds:
        logger.info("附注事实 …")
        statement_roles: dict[str, float] = {}
        for statement in tables.values():
            statement_roles.update(roles_from_rows(statement.get("rows") or [], unit=statement.get("unit") or "元"))
        notes_table, notes_checks = extract_notes(agent, toc, statement_roles)
        if notes_table is not None:
            tables[NOTES] = notes_table
            all_checks.extend([*notes_checks, verbatim_check(NOTES, notes_table, report_text)])
            rows = notes_table.get("rows") or []
            logger.info(f"{NOTES}: {len(rows)} 行, {len(roles_from_rows(rows, unit=notes_table.get('unit') or '元'))} 个 role\n{check_report(notes_checks)}")

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
    parser.add_argument("--kind", action="append", choices=_ALL_KINDS, help="只重取某几张表（可重复），其余沿用已有产物")
    args = parser.parse_args()

    derived = Path(args.root) / args.market / args.stock / "derived"
    mds = sorted(derived.glob("*.md"))
    if not mds:
        raise SystemExit(f"{derived} 没有年报 md，先跑 ocr/cli.py")

    kinds = tuple(args.kind) if args.kind else _ALL_KINDS
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
