"""facts：事实层——契约与抽取

契约（无版本号；契约变了就删产物重算）：
    fields     固定字段：准则强制列报、且挂着判据的行。只有这一层参与算术，跨公司可比性由它承担。
    details    按需明细：公司特有、行名与含义各异，不做字段映射，只做逐字校验，供叙述引用。
    checks     校验结论：恒等式、逐字、页码可溯源。未通过的进 unresolved，下游按缺数处理。
    segments   一段事实 = 一张报表或一节附注，页码随段带出（表级页码已够溯源，逐值页码要多花 decode）。

抽取：模型读原表抄成紧凑四元数组 → 程序跑校验 → 把失败的具体形态（哪一行让等式差多少、
哪一行取相反数就能对上、原表披露了但没抄的行）反馈回去重试，只在变好时采纳。
"""

from typing import TypedDict


class Value(TypedDict):
    """一个字段的两期值：本期在前、上期在后；缺数为 None。"""

    value: float | None
    prev: float | None


class Detail(TypedDict):
    """按需明细：行名照原文，不做字段映射。"""

    label: str
    value: float | None
    prev: float | None
    kind: str
    page: int | None


class CheckResult(TypedDict):
    """一条校验结论。status 取 pass / fail / skip / diff。"""

    id: str
    desc: str
    status: str
    delta: float | None
    missing: list[str]


class Segment(TypedDict):
    """一段事实（一张报表或一节附注）。"""

    kind: str
    title: str
    consolidated: bool
    unit: str
    pages: list[int]
    columns: list[str]
    fields: dict[str, Value]
    details: list[Detail]


class Facts(TypedDict):
    """一份年报的事实。"""

    year: int
    source: dict
    segments: list[Segment]
    fields: dict[str, Value]
    details: list[Detail]
    checks: list[CheckResult]
    unresolved: list[str]

import argparse
import json
import logging
import re
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path

from cagent.checks import IDENTITIES, Check, check_report, rollforward_check, verbatim_check, verify
from cagent.chunk import Toc, report_year, split_pages
from cagent.fields import ROLES, ROLES_BY_KIND, UNIT_SCALE, duplicate_roles, number, roles_from_rows
from cagent.llm import LLM
from cagent.scraper.lakehouse_index import atomic_write

logger = logging.getLogger(__name__)

_IDENTITY_BY_ID = {identity.id: identity for identity in IDENTITIES}


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


def pick_tables(agent: LLM, inventory: list[dict]) -> dict[str, list[str]]:
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
        - dividend：利润分配下「对股东的分配」行，取**未分配利润列**的金额，写成正数（等式里按减项用；不是股东权益合计列）
        - capital_raised：「所有者投入和减少资本」行，取归母权益小计列；表中没有小计列时取股东权益合计列。
          这一项照原表符号抄：增资为正、回购或减少资本为负

        - 抄录原表数值，禁止计算、禁止改数；千分位逗号去掉，括号负数改成负号
        - unit 照抄原表单位；两项都找不到就不输出行
        - columns 填 [本期, 上期]；rows 只放上面这两行
        - consolidated 填 true 仅当你抄的是合并口径那张表；母公司（公司）表填 false
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


def _extract_tail() -> str:
    return "<task>按上面的四元数组格式输出全部 role 行。</task>"


def _repair_tail(table: dict, feedback: str) -> str:
    current = [[row.get("label"), row.get("role"), *[v for v in (row.get("values") or [None, None])[:2]]] for row in table.get("rows") or []]
    return textwrap.dedent(f"""
        <current>
        {json.dumps(current, ensure_ascii=False)}
        </current>

        <task>
        上面这些数过不了校验：

        {feedback}

        回 <tables> 逐行核对后，**只输出需要改正的那几行**（同样的四元数组）；没把握的行照原表重抄一遍。
        </task>""").strip()


def _sign_hints(identity, roles: dict[str, float]) -> list[str]:
    """哪一行取相反数就能对上——把符号问题定位到具体行，不靠提示词里的通则。"""
    delta = sum(sign * float(roles.get(role) or 0.0) for role, sign in identity.terms)
    hints = []
    for role, sign in identity.terms:
        value = float(roles.get(role) or 0.0)
        if value == 0:
            continue
        flipped = delta - 2 * sign * value
        if abs(flipped) < abs(delta) * 0.5:
            hints.append(f"{role} 现在是 {value:,.2f}，取相反数后 Δ 从 {delta:,.2f} 变为 {flipped:,.2f}")
    return hints


def segment_feedback(kind: str, table: dict | None, known_roles: dict[str, float] | None = None) -> tuple[int, str]:
    """这一段的分数与反馈：分数越低越好，反馈要具体到行与数。

    反馈三样东西，都是程序从校验结果里算出来的，不是通用规则：
    失败的等式（含参与项的值）、可疑的符号（取反后残差明显变小）、原表披露但没抄的行。
    """
    if table is None:
        return 3, "上一轮没有返回可解析的表。"
    roles = {**(known_roles or {}), **roles_from_rows(table.get("rows") or [], unit=table.get("unit") or "元")}
    checks = [check for check in verify(roles) if check.id.startswith(kind)]
    failures = [check for check in checks if check.status == "fail"]
    # 只提示"同一等式里多数项都在、只缺一两行"的漏抄：整张表都没有的行不算漏
    missing: list[str] = []
    for check in checks:
        if check.status != "skip":
            continue
        identity = _IDENTITY_BY_ID[check.id]
        present = [role for role, _ in identity.terms if role in roles]
        if len(present) >= max(2, round(len(identity.terms) * 0.6)):
            missing += list(check.missing)
    missing = sorted(set(missing))[:3]
    scope_ok = table.get("consolidated") is True
    score = len(failures) * 2 + (0 if scope_ok else 1) + min(len(missing), 2)
    lines = []
    for check in failures:
        identity = _IDENTITY_BY_ID[check.id]
        terms = "；".join(f"{role}={roles.get(role, 0.0):,.2f}" for role, _ in identity.terms)
        lines.append(f"- {check.id}（{identity.desc}）Δ={check.delta:,.2f}\n  {terms}")
        hints = _sign_hints(identity, roles)
        if hints:
            lines.append("  符号可疑：" + "；".join(hints))
    if missing:
        lines.append("- 原表若披露了这些行就该抄上：" + "、".join(ROLES.get(role, role) for role in missing))
    if not scope_ok:
        lines.append("- 本段要的是合并报表口径（母公司报表同样满足恒等式，口径却不对）")
    return score, "\n".join(lines) or "无"


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


def extract_kind(agent: LLM, kind: str, inventory: list[dict], ids: list[str], attempts: int = 3, passes: int = 2) -> tuple[dict | None, list[Check]]:
    """抽一张表并反复校验：校验驱动的收敛，只在变好时采纳。

    模型有随机性：一轮收敛没成功时，整段重抄一遍（带上上一遍的失败反馈）比在同一份结果上继续打补丁更有用。
    权益变动表只提两笔事实、且要靠利润表与资产负债表的数才能校验，故不走这张表的收敛循环，
    残差在跨表校验里报。
    """
    html = _tables_html(inventory, ids)
    if not html:
        return None, []
    table = _normalize_table(agent.chat_json(_equity_prompt(html), _extract_schema())) if kind == "equity" else None
    if kind == "equity":
        return table, []

    def check(value: dict) -> tuple[int, str]:
        return segment_feedback(kind, value)

    best: tuple[dict, int, str] | None = None
    carried = ""
    for pass_no in range(1, passes + 1):
        prefix = _statement_prefix(kind, html) + (f"\n\n<previous-attempt>\n上一遍抄的结果过不了校验：{carried}\n请重新逐行核对原表。\n</previous-attempt>" if carried else "")
        task = agent.task(prefix)

        def tail(previous: dict | None, feedback: str) -> str:
            return _extract_tail() if previous is None else _repair_tail(previous, feedback)

        table, score, feedback = task.converge(tail, parse=lambda raw, previous: _merge_candidate(previous, json.loads(raw)), check=check,
                                               schema=lambda round_no, previous: _extract_schema() if previous is None else _patch_schema(),
                                               attempts=attempts)
        if table is None:
            continue
        if best is None or score < best[1]:
            best = (table, score, feedback)
        if score == 0:
            logger.info(f"{kind}：第 {pass_no} 遍收敛")
            break
        carried = feedback[:300]
        logger.warning(f"{kind}：第 {pass_no} 遍未收敛（分数 {score}），重抄一遍")
    if best is None:
        return None, []
    table, score, feedback = best
    roles = roles_from_rows(table["rows"], unit=table.get("unit") or "元")
    if score > 0:
        logger.warning(f"{kind} 收敛未完成（分数 {score}）：{feedback.splitlines()[0] if feedback else ''}")
    return table, [check for check in verify(roles) if check.id.startswith(kind)]


def _failure_count(kind: str, table: dict, known_roles: dict[str, float] | None = None) -> int:
    """这张表当前有几条恒等式不成立——用来判断重取有没有变好。"""
    roles = {**(known_roles or {}), **roles_from_rows(table.get("rows") or [], unit=table.get("unit") or "元")}
    return sum(1 for check in verify(roles) if check.id.startswith(kind) and check.status == "fail")


def _merge_candidate(previous: dict | None, raw: dict, columns: list[str] | None = None) -> dict:
    """把一轮回复并成候选表：首轮整表照收，重取只回错行则按 role 并回原表。"""
    table = _normalize_table(raw, columns)
    if previous is None:
        return table
    merged = {**previous, "rows": _merged_rows(previous.get("rows") or [], table.get("rows") or [])}
    for key in ("title", "unit", "pages", "columns", "consolidated"):
        if raw.get(key) is not None:
            merged[key] = table.get(key) or raw.get(key)
    return merged


def _normalize_table(raw: dict, columns: list[str] | None = None) -> dict:
    """模型返回的紧凑表 → 规范表（行变 dict，columns 变字符串列表）。"""
    columns = [str(c) for c in (raw.get("columns") or columns or [])]
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


def extract_notes(agent: LLM, toc: Toc, known_roles: dict[str, float] | None = None, max_repair: int = 2) -> tuple[dict | None, list[Check]]:
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
    selected = _fit_chunks(agent, [chunks[i] for i in choices] + [chunks[i] for i in anchored])
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

    known = known_roles or {}
    notes_task = agent.task(prefix)

    def tail(previous: dict | None, feedback: str) -> str:
        if previous is None:
            return "<task>按四元数组输出上述事实。</task>"
        current = [[row.get("label"), row.get("role"), *[v for v in (row.get("values") or [None, None])[:2]]] for row in previous.get("rows") or []]
        verified = "；".join(f"{role}={value:,.2f}（来自主表，已核实，不要改）" for role, value in known.items() if role in {r for check in verify(known) for r, _ in _IDENTITY_BY_ID[check.id].terms})
        return textwrap.dedent(f"""
            <current>
            {json.dumps(current, ensure_ascii=False)}
            </current>

            <task>
            上面这几项与主表对不上：

            {feedback}

            已核实的主表数：{verified}

            回 <segments> 逐项核对后，**只输出需要改正的那几行**（同样的四元数组）。
            </task>""").strip()

    table, score, _ = notes_task.converge(
        tail,
        parse=lambda raw, previous: _merge_candidate(previous, json.loads(raw)),
        check=lambda value: segment_feedback(NOTES, value, known),
        schema=lambda round_no, previous: _extract_schema() if previous is None else _patch_schema(),
        attempts=max_repair + 1,
    )
    if table is None:
        return None, []
    checks = [check for check in verify({**known, **roles_from_rows(table["rows"], unit=table.get("unit") or "元")}) if check.id.startswith("notes")]
    if score > 0:
        logger.warning(f"附注收敛未完成（分数 {score}）")
    return table, checks


@dataclass(frozen=True)
class DetailNeed:
    """一项按需明细：要什么、去哪儿找、检索关键词。

    明细不做字段映射（行名照原文），所以"要什么"由需要它的那个问题定义，不是全局词表。
    """

    id: str
    title: str
    instruction: str
    anchors: tuple[str, ...]


DETAIL_NEEDS: tuple[DetailNeed, ...] = (
    DetailNeed(
        id="transfer",
        title="资产怎么变成费用：折旧摊销、计提减值、转固、核销",
        instruction=(
            "列出本期与上期这些转化事实：固定资产折旧、无形资产摊销、使用权资产折旧、长期待摊费用摊销；"
            "各项资产减值准备的本期计提额与转回/转销额；在建工程转入固定资产的金额；"
            "应收账款坏账准备的计提与核销金额。行名照原文。"
        ),
        anchors=("累计折旧", "减值准备", "本期计提", "在建工程", "转入固定资产", "核销", "摊销"),
    ),
    DetailNeed(
        id="obligation",
        title="没入表的义务：对外担保、未决诉讼、承诺、衍生品、受限资产",
        instruction=(
            "列出这些表外义务的金额与对象：对外担保余额（含对子公司的担保）、作为被告的未决诉讼标的金额、"
            "已签约未拨备的重大承诺（购建长期资产、对外投资）、衍生金融资产与负债的期末公允价值、"
            "使用受限的货币资金与其他受限资产。行名照原文，金额照抄。"
        ),
        anchors=("对外担保", "担保总额", "未决诉讼", "重大承诺", "衍生金融", "所有权受到限制"),
    ),
    DetailNeed(
        id="related",
        title="占着资本不产出的资产：其他应收款与大额占款",
        instruction=(
            "列出其他应收款的构成：按对象或性质列示的余额较大的几项（如关联方往来、押金保证金、备用金、代垫款），"
            "以及账龄较长的部分；再列出资产端金额明显偏大但与主业无关的项目（如闲置资产、待处置资产）。行名照原文。"
        ),
        anchors=("其他应收款", "账龄", "关联方", "押金", "保证金", "闲置"),
    ),
)
DETAIL_NEED_BY_ID = {need.id: need for need in DETAIL_NEEDS}


def _fit_chunks(llm: LLM, chunks: list[str], reserve_tokens: int = 6144) -> list[str]:
    """按上下文预算取片段：prefill 可以奢侈，但不能把上下文撑爆。

    预算按字符估（1 token ≈ 1.6 字符），装不下的片段直接跳过而不是截断——截断的原文比没有更危险。
    """
    budget_chars = max(4096, int((llm.n_ctx() - reserve_tokens) * 1.6))
    picked, used = [], 0
    for chunk in chunks:
        if used + len(chunk) > budget_chars:
            continue
        picked.append(chunk)
        used += len(chunk)
    logger.info(f"片段预算 {budget_chars:,} 字符：取 {len(picked)}/{len(chunks)} 段（{used:,} 字符）")
    return picked


def _detail_schema() -> dict:
    """明细行：[行名, 本期数, 上期数, 页码]——不做 role 映射，页码随行带出。"""
    return {
        "type": "object",
        "properties": {
            "unit": {"type": "string"},
            "pages": {"type": "array", "items": {"type": "integer"}},
            "rows": {"type": "array", "items": {"type": "array", "items": {"type": ["string", "number", "null"]}}},
        },
        "required": ["unit", "pages", "rows"],
    }


def extract_details(agent: LLM, toc: Toc, need: DetailNeed, *, attempts: int = 2, limit: int = 20) -> dict:
    """按需明细：检索片段 → 抄成 [行名, 本期, 上期, 页码] → 逐字校验（数字必须在片段里出现）。

    不做字段映射，所以只验"数是不是原文里的"；页码允许落在片段页码集合内。
    """
    try:
        picked = sorted(set(toc.pick_chapters(toc.chapters, need.title)))
    except ValueError:
        logger.warning(f"明细 {need.id}：章节定位失败")
        return {}
    chunks = [chunk for index in picked for chunk in toc.chapter_text_chunk(index, budget=6144)]
    hits = [chunk for chunk in chunks if any(anchor in chunk for anchor in need.anchors)]
    ordered = hits + [chunk for chunk in chunks if chunk not in hits]
    selected = _fit_chunks(agent, ordered)
    if not selected:
        logger.warning(f"明细 {need.id}：检索不到相关片段")
        return {}
    html = "\n\n".join(selected)
    logger.info(f"明细 {need.id}：片段 {len(chunks)} → 选中 {len(selected)}")

    prefix = textwrap.dedent(f"""
        <requirement>
        你在读年报附注，只列出下面这一类事实。只抄原文写明的数，禁止计算、禁止估算、禁止改数。

        - 行用四元数组：[原表行名, 本期数, 上期数, 页码]（页码是正文 <!-- page N --> 标记的页号）
        - 行名照原文，不要归纳、不要改写成通用名称；原文没有的行不要编，没有上期数就填 null
        - 千分位逗号去掉，括号负数改成负号；unit 照抄原表单位（元/千元/百万元）
        - 最多 12 行，优先金额大的、与这一类直接相关的
        </requirement>

        <need>
        {need.title}
        {need.instruction}
        </need>

        <segments>
        {html}
        </segments>""").strip()

    task = agent.task(prefix)

    def tail(_previous: dict | None, feedback: str) -> str:
        if not feedback:
            return "<task>按四元数组列出这一类事实。</task>"
        return f"<violations>\n上一稿里这些数不在上面的原文里，或页码不对：{feedback}\n只许抄原文有的数重列一遍。\n</violations>"

    def check(value: dict) -> tuple[int, str]:
        outside = verbatim_check(need.id, {"rows": value.get("rows") or []}, html)
        pages = {int(match) for match in re.findall(r"<!-- page (\d+) -->", html)}
        rows = value.get("rows") or []
        bad_pages = [str(int(row["page"])) for row in rows if row.get("page") and pages and int(row["page"]) not in pages]
        no_page = [str(row.get("label")) for row in rows if not row.get("page")]
        problems = list(outside.missing)
        if bad_pages:
            problems.append(f"页码不在片段内：{'、'.join(bad_pages[:3])}")
        if no_page:
            problems.append(f"这些行没给页码：{'、'.join(no_page[:3])}")
        return len(problems), "；".join(problems)

    def parse(raw: str, _previous: dict | None) -> dict:
        payload = json.loads(raw)
        rows = []
        for cells in payload.get("rows") or []:
            cells = list(cells) + [None] * (4 - len(cells))
            rows.append({"label": str(cells[0] or "").strip(), "values": [number(cells[1]), number(cells[2])], "page": number(cells[3])})
        return {"unit": payload.get("unit") or "元", "pages": payload.get("pages") or [], "rows": [row for row in rows if row["label"]]}

    table, score, feedback = task.converge(tail, parse=parse, check=check, schema=_detail_schema(), max_tokens=1024, attempts=attempts)
    if table is None:
        return {}
    if score > 0:
        logger.warning(f"明细 {need.id} 仍有 {score} 处没对上：{feedback[:120]}")
    table["need"] = need.id
    table["title"] = need.title
    return table


def extract_financials(
    agent: LLM,
    md_path: str | Path,
    root: Path,
    market: str = "",
    stock: str = "",
    kinds: tuple[str, ...] = _ALL_KINDS,
    base: dict | None = None,
    details: tuple[str, ...] = (),
) -> dict:
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

    segments: list[dict] = [segment for segment in (base or {}).get("segments") or [] if segment.get("kind") not in kinds]
    # 沿用旧结论时，必须把这一轮重取的 kind 相关结论全部丢掉，否则上一轮的 fail 会一直挂着
    def check_kind(check_id: str) -> str | None:
        parts = check_id.split(".")
        return next((part for part in parts if part in _ALL_KINDS), None)

    all_checks: list[Check] = [
        Check(**check)
        for check in (base or {}).get("checks", [])
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
        segments.append({**table, "kind": kind})
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
        for statement in segments:
            statement_roles.update(roles_from_rows(statement.get("rows") or [], unit=statement.get("unit") or "元"))
        notes_table, notes_checks = extract_notes(agent, toc, statement_roles)
        if notes_table is not None:
            segments.append({**notes_table, "kind": NOTES})
            all_checks.extend([*notes_checks, verbatim_check(NOTES, notes_table, report_text)])
            rows = notes_table.get("rows") or []
            logger.info(f"{NOTES}: {len(rows)} 行, {len(roles_from_rows(rows, unit=notes_table.get('unit') or '元'))} 个 role\n{check_report(notes_checks)}")

    if details:
        detail_rows = list((base or {}).get("details") or [])
        for need_id in details:
            need = DETAIL_NEED_BY_ID.get(need_id)
            if need is None:
                logger.warning(f"没有这项按需明细：{need_id}")
                continue
            logger.info(f"按需明细：{need.title} …")
            table = extract_details(agent, toc, need)
            if not table:
                continue
            unit = table.get("unit") or "元"
            scale = UNIT_SCALE.get(unit.strip(), 1.0)
            detail_rows = [row for row in detail_rows if row.get("kind") != need_id]
            for row in table["rows"]:
                values = row.get("values") or [None, None]
                detail_rows.append(
                    {
                        "label": row["label"],
                        "value": (values[0] * scale) if values[0] is not None else None,
                        "prev": (values[1] * scale) if len(values) > 1 and values[1] is not None else None,
                        "kind": need_id,
                        "title": need.title,
                        "page": int(row["page"]) if row.get("page") else None,
                    }
                )
            logger.info(f"明细 {need_id}：{len(table['rows'])} 行")

    roles: dict[str, float] = {}
    roles_prior: dict[str, float] = {}
    for table in segments:
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
            "pages": {segment["kind"]: segment.get("pages") for segment in segments},
            "picked": picked_tables,
        },
        "unit": next((segment.get("unit") for segment in segments if segment.get("unit")), "元"),
        "segments": segments,
        "checks": [check.__dict__ for check in all_checks],
        "unresolved": unresolved,
        "details": detail_rows if details else (base or {}).get("details") or [],
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
    parser.add_argument("--details", default="", help="按需明细，逗号分隔（transfer/obligation/related）；空则不抽")
    parser.add_argument("--kind", action="append", choices=_ALL_KINDS, help="只重取某几张表（可重复），其余沿用已有产物")
    args = parser.parse_args()

    derived = Path(args.root) / args.market / args.stock / "derived"
    mds = sorted(derived.glob("*.md"))
    if not mds:
        raise SystemExit(f"{derived} 没有年报 md，先跑 ocr/cli.py")

    kinds = tuple(args.kind) if args.kind else _ALL_KINDS
    agent = LLM(trace=True)
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
