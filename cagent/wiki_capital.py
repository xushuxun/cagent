"""capital 模块：资本回报 writer

逐年提取披露的原始数字（LLM 照抄披露口径，禁止换算与编撰），跨年合计、比率、均值
全部由程序计算（数字经过程序核验），组装"资本回报"栏目：历年与累计、资本配置、
假设试算。产物落位 knowledge/fy<最新年>/capital.json。

用法：
    uv run cagent/wiki_capital.py --stock 601633 --market cn
    uv run cagent/wiki_capital.py --stock 601633 --market cn --force
"""

import argparse
import json
import logging
import sys
import textwrap
from pathlib import Path

from cagent.agent import Agent
from cagent.chunk import Toc, report_year

logger = logging.getLogger(__name__)

_pick_task = "你在通读一份上市公司年报的目录。提取这些披露数字：归母净利润、折旧与摊销、购建长期资产支付的现金、现金分红、经营活动现金流量净额、总股本、回购注销股数，需要阅读哪些章节？"

_extract_prompt_a = textwrap.dedent("""
    你在阅读一份上市公司年报的节选，提取披露数字：归属于上市公司股东的净利润、
    经营活动产生的现金流量净额、购建固定资产无形资产和其他长期资产支付的现金、年末总股本、当年回购并注销的股份数量。
    照抄披露数值，金额单位为元、股数单位为股，取本年数、不取上年数；禁止编撰，节选里没有的字段留空。
    """).strip()

_extract_prompt_b = textwrap.dedent("""
    你在阅读一份上市公司年报的节选，只提取折旧与摊销的各组成行
    （固定资产折旧、使用权资产折旧、无形资产摊销、长期待摊费用摊销等，见现金流量表补充资料）。
    照抄披露数值（元），取本期金额、不取上年同期数；节选里没有则空数组。
    """).strip()

_dividend_pick_task = "你在通读一份上市公司年报的目录。了解本报告期的利润分配：中期分红与年度利润分配预案的现金分红总额，需要阅读哪些章节？现金分红情况表（每10股派息、现金分红金额、占净利润比率）通常在公司治理章节；财务报告/审计报告章节只有实施口径的陷阱数字，不要选。"

_dividend_prompt = textwrap.dedent("""
    你在阅读一份上市公司年报的节选，只提取归属于本报告期利润的各笔现金分红总额：
    本报告期中期分红与本报告期年度利润分配（末期）预案金额，照抄披露（含税总额，元）。
    每笔标注其归属的分红年度（利润所属年度，不是派发实施年份），并照抄金额所在的原文短语作为依据。
    只填披露了总额的分红；只有每股/每10股派息金额而没有总额的，不填。
    节选里没有则空数组。complete=节选读完后，本报告期的中期与末期分红总额是否都已有着落。

    <examples>
    <example>
    <pages>
    ### 未分配利润
    2022年度: 年初未分配利润 41,892,707,709.74；加:本年归属于母公司股东的净利润 8,266,041,808.18；
    减:分派现金股利 641,564,045.06（2022年6月2021年年度股东大会通过2021年度利润分配方案，每股派0.07元）。
    </pages>
    <output>
    {"dividend_items": [], "complete": false}
    </output>
    </example>
    <example>
    <pages>
    ### 本报告期利润分配预案
    每10股派息数(元)(含税) 4.5；现金分红金额(含税) 3,853,006,428.15；分红年度合并报表中归属于上市公司普通股股东的净利润 12,692,204,172.58。
    </pages>
    <output>
    {"dividend_items": [{"item": "年度利润分配（末期）预案", "year": 2022, "value": 3853006428.15, "evidence": "现金分红金额(含税) 3,853,006,428.15"}], "complete": true}
    </output>
    </example>
    </examples>
    """).strip()

_extract_schema_a = {
    "type": "object",
    "properties": {
        "net_profit": {"type": ["number", "null"], "description": "归属于上市公司股东的净利润，元"},
        "capex_cash": {"type": ["number", "null"], "description": "购建固定资产、无形资产和其他长期资产支付的现金，元"},
        "operating_cashflow": {"type": ["number", "null"], "description": "经营活动产生的现金流量净额，元"},
        "total_shares": {"type": ["number", "null"], "description": "年末总股本，股"},
        "buyback_cancelled": {"type": ["number", "null"], "description": "当年回购并注销的股份数量，股"},
    },
    "required": ["net_profit", "capex_cash", "operating_cashflow", "total_shares", "buyback_cancelled"],
}

_item_b = {"type": "object", "properties": {"item": {"type": "string"}, "value": {"type": "number"}}, "required": ["item", "value"]}
_extract_schema_b = {
    "type": "object",
    "properties": {
        "depreciation_items": {"type": "array", "items": _item_b, "description": "折旧与摊销各组成行，元"},
    },
    "required": ["depreciation_items"],
}
_dividend_item = {"type": "object", "properties": {"item": {"type": "string"}, "year": {"type": "integer", "description": "归属的分红年度（利润所属年度）"}, "value": {"type": "number"}, "evidence": {"type": "string", "description": "金额所在的原文短语"}}, "required": ["item", "year", "value", "evidence"]}
_dividend_schema = {
    "type": "object",
    "properties": {
        "dividend_items": {"type": "array", "items": _dividend_item, "description": "归属于本报告期利润的各笔现金分红总额（中期+末期预案），元"},
        "complete": {"type": "boolean", "description": "本报告期的中期与末期分红总额是否都已有着落"},
    },
    "required": ["dividend_items", "complete"],
}

_REQUIRED = ["net_profit", "depreciation_items", "capex_cash", "operating_cashflow", "total_shares"]
_FIELD_NAMES = {"net_profit": "归母净利润", "depreciation_items": "折旧与摊销", "capex_cash": "购建长期资产支付的现金", "dividend_items": "现金分红", "operating_cashflow": "经营活动现金流量净额", "total_shares": "年末总股本"}


def _scan_chunks(agent: Agent, toc: Toc, picked: list[int], raw: dict, dep_lists: list) -> None:
    """逐 chunk 两路提取：A 单值字段先到先得；B 折摊清单全部收集，事后取舍。"""
    for chunk in (c for i in picked for c in toc.chapter_text_chunk(i)):
        for key, value in agent.chat_json(f"{_extract_prompt_a}\n\n<pages>\n{chunk}\n</pages>", _extract_schema_a).items():
            if key not in raw and value not in (None, []):
                raw[key] = value
        b = agent.chat_json(f"{_extract_prompt_b}\n\n<pages>\n{chunk}\n</pages>", _extract_schema_b)
        if b.get("depreciation_items"):
            dep_lists.append(b["depreciation_items"])


def _settle_lists(raw: dict, dep_lists: list) -> None:
    """折摊取阅读顺序第一份非空清单（年报附注合并报表在先、母公司在后）。"""
    if "depreciation_items" not in raw and dep_lists:
        raw["depreciation_items"] = dep_lists[0]


def _extract_dividend(agent: Agent, md_path: Path, force: bool) -> list:
    """分红独立通道：只在利润分配相关章节里找（缓存到 derived/<stem>.dividend.json）。

    实施口径的陷阱数字集中在财务报告章节，不扫它；取阅读顺序第一份非零清单
    同一笔分红常在多个章节重复披露（董事会报告、公司治理的现金分红情况表），
    也有陷阱数字（未分配利润变动表、股利附注里的上年方案实施数）；模型标注归属年度，程序按报告期过滤。
    某节选自评中期与末期总额已齐（complete）即可停；收集到的各笔最后让模型合并去重。
    找不到即空（当年未分红）。
    """
    cache = md_path.with_suffix(".dividend.json")
    if cache.exists() and not force:
        return json.loads(cache.read_text(encoding="utf-8"))["dividend_items"]
    year = report_year(agent, md_path)
    toc = Toc(agent, md_path)
    found = []  # (章节起始 md_page, 章节标题, dividend_items)
    complete = False

    def scan(chunks: list[str], md_page: int, title: str) -> None:
        nonlocal complete
        for chunk in chunks:
            r = agent.chat_json(f"这份年报的报告期是{year}年度。\n{_dividend_prompt}\n\n<pages>\n{chunk}\n</pages>", _dividend_schema)
            lst = [item for item in r.get("dividend_items", []) if item.get("year") == year]
            if lst:
                found.append((md_page, title, lst))
            complete = complete or (r.get("complete") and bool(lst))

    # 卷首（重要提示等，第一章之前）：利润分配预案摘要常在这里，不在任何章节内
    front = toc.text[: toc.text.index(f"<!-- page {toc.chapters[0]['md_page']} -->")]
    scan([front[i : i + 32768] for i in range(0, len(front), 32768)], 0, "卷首")

    remaining = list(range(len(toc.chapters)))
    tried_titles = []
    for _ in range(3):
        if not remaining or complete:
            break
        task = _dividend_pick_task
        if tried_titles:
            task += f"（已读过「{'」「'.join(tried_titles)}」，本报告期分红总额还没着落，换其他章节）"
        picked = toc.pick_chapters([toc.chapters[i] for i in remaining], task)
        chosen = [remaining[i] for i in picked]
        logger.info(f"分红选中章: {[toc.chapters[i]['title'] for i in chosen]}")
        for i in chosen:
            scan(toc.chapter_text_chunk(i), toc.chapters[i]["md_page"], toc.chapters[i]["title"])
        tried_titles += [toc.chapters[i]["title"] for i in chosen]
        remaining = [i for i in remaining if i not in chosen]
    found.sort(key=lambda x: x[0])
    items = [item for _, _, lst in found for item in lst]
    if len(found) > 1:
        # 同一笔分红在多章节重复披露，让模型按依据合并去重
        briefing = "\n".join(f"- 章节「{title}」: {json.dumps(lst, ensure_ascii=False)}" for _, title, lst in found)
        merged = agent.chat_json(
            textwrap.dedent(f"""
                这是同一份{year}年度年报不同章节提取的、归属于{year}年度利润的现金分红（可能重复披露同一笔，也可能混入口径不一致的数字）。
                按 evidence 原文合并去重：同一笔分红取披露最完整的一笔；输出最终各笔（中期+末期预案），照抄金额（元）。

                {briefing}""").strip(),
            {"type": "object", "properties": {"dividend_items": {"type": "array", "items": _dividend_item}}, "required": ["dividend_items"]},
        )
        items = merged["dividend_items"]
    cache.write_text(json.dumps({"dividend_items": items}, ensure_ascii=False, indent=1), encoding="utf-8")
    return items


def _extract_year(agent: Agent, md_path: Path, force: bool) -> dict:
    """单年提取：问模型照抄披露数字（缓存到 derived/<stem>.capital.json）。缺字段按字段二次选章补漏。"""
    cache = md_path.with_suffix(".capital.json")
    if cache.exists() and not force:
        return json.loads(cache.read_text(encoding="utf-8"))
    toc = Toc(agent, md_path)
    raw, dep_lists = {}, []

    picked = toc.pick_chapters(toc.chapters, _pick_task)
    logger.info(f"选中章: {[toc.chapters[i]['title'] for i in picked]}")
    _scan_chunks(agent, toc, picked, raw, dep_lists)
    _settle_lists(raw, dep_lists)

    missing = [k for k in _REQUIRED if k not in raw]
    if missing:
        task = f"你在通读一份上市公司年报的目录。提取这些披露数字：{'、'.join(_FIELD_NAMES[k] for k in missing)}，需要阅读哪些章节？"
        picked = toc.pick_chapters(toc.chapters, task)
        logger.info(f"补漏 {missing}，选中章: {[toc.chapters[i]['title'] for i in picked]}")
        _scan_chunks(agent, toc, picked, raw, dep_lists)
        _settle_lists(raw, dep_lists)

    still_missing = [k for k in _REQUIRED if k not in raw]
    if still_missing:
        raise ValueError(f"{md_path.name} capital 提取缺字段: {still_missing}")
    cache.write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")
    return raw


def _num(v: float) -> str:
    return f"{v:,.1f}"


def build_doc(per_year: dict[int, dict]) -> str:
    """跨年汇总全部由程序计算，组装栏目 HTML。缺字段宁可报错也不凑数。"""
    years = sorted(per_year)
    missing = [(y, k) for y in years for k in _REQUIRED if k not in per_year[y]]
    if missing:
        raise ValueError(f"capital 提取缺字段: {missing}")

    rows = []
    for y in years:
        r = per_year[y]
        np_, da, capex = r["net_profit"] / 1e8, sum(i["value"] for i in r["depreciation_items"]) / 1e8, r["capex_cash"] / 1e8
        div = sum(i["value"] for i in r["dividend_items"]) / 1e8
        rows.append({"year": y, "np": np_, "da": da, "capex": capex, "oe": np_ + da - capex, "div": div, "ret": np_ - div, "ocf": r["operating_cashflow"] / 1e8,
                     "shares": r["total_shares"] / 1e8, "buyback": (r.get("buyback_cancelled") or 0) / 1e8})
    n, first, last = len(years), years[0], years[-1]
    total = {k: sum(r[k] for r in rows) for k in ("np", "da", "capex", "oe", "div", "ret", "ocf")}
    buyback_total = sum(r["buyback"] for r in rows)
    delta, rr, mean = rows[-1]["np"] - rows[0]["np"], (rows[-1]["np"] - rows[0]["np"]) / total["ret"] * 100, total["oe"] / n

    def tr(label: str, key: str, hl: bool = False) -> str:
        cells = "".join(f'<td class="n">{_num(r[key])}</td>' for r in rows)
        return f'<tr{" class=\"hl\"" if hl else ""}><td>{label}</td>{cells}<td class="n">{_num(total[key])}</td></tr>'

    buyback_fact = f"（回购注销 {buyback_total:.2f} 亿股）" if buyback_total else ""
    return f"""<div class="note">本节货币单位为人民币亿元。</div>
<h3>历年与累计</h3>
<table class="stats">
<tr><th>指标（亿元）</th>{"".join(f"<th>{y}</th>" for y in years)}<th>{n}年累计</th></tr>
{tr("归母净利润", "np", hl=True)}
{tr("折旧与摊销", "da")}
{tr("购建长期资产付现", "capex")}
{tr("所有者收益", "oe")}
{tr("现金分红", "div")}
{tr("留存利润", "ret")}
{tr("经营现金流净额", "ocf")}
</table>
<div class="note">所有者收益 ＝ 归母净利润 ＋ 折旧摊销 − 购建长期资产付现。{n}年间总股本由 {rows[0]["shares"]:.2f} 亿股至 {rows[-1]["shares"]:.2f} 亿股{buyback_fact}，作为资本配置事实列示，不做每股折算。</div>

<h3>资本配置</h3>
<div class="chain">
<div class="step"><b>{n}年归母净利润 · {_num(total["np"])} 亿</b>{first}–{last} 各年合计</div>
<div class="to">→</div>
<div class="step"><b>分红 · {_num(total["div"])} 亿</b>占净利润 {total["div"] / total["np"] * 100:.1f}%</div>
<div class="to">＋</div>
<div class="step"><b>留存 · {_num(total["ret"])} 亿</b>占净利润 {total["ret"] / total["np"] * 100:.1f}%</div>
</div>
<div class="return">另一口径：{n}年累计所有者收益 {_num(total["oe"])} 亿，占净利润 {total["oe"] / total["np"] * 100:.1f}%；与净利润的差异为折旧摊销与购建付现的净额</div>
<div class="nums">
<div><dt>分红（{n}年累计）</dt><dd>{_num(total["div"])} 亿</dd></div>
<div><dt>回购注销</dt><dd>{buyback_total:.2f} 亿股</dd></div>
<div><dt>总股本</dt><dd>{rows[-1]["shares"]:.2f} 亿股<small>{rows[0]["shares"]:.2f} → {rows[-1]["shares"]:.2f}</small></dd></div>
</div>
<div class="nums">
<div><dt>累计留存</dt><dd>{_num(total["ret"])} 亿</dd></div>
<div><dt>{last} 年净利润较 {first} 年</dt><dd>{delta:+.1f} 亿</dd></div>
<div><dt>增量 ÷ 留存</dt><dd>{rr:.1f}%</dd></div>
</div>

<h3>假设试算</h3>
<div class="note">本节为假设推演，非估值结论。默认取值来自披露与计算，不可改；滑钮试算，可一键恢复。</div>
<div class="calc">
<table>
<tr><th>假设</th><th>默认取值</th><th>试算取值</th><th><button type="button" class="calc-reset">恢复默认</button></th></tr>
<tr><td>未来每年赚到的现金（{n}年均值）</td><td class="n">{mean:.1f} 亿</td><td class="n cur" data-for="e"><span data-e></span> 亿</td><td class="sld"><input type="range" data-in="e" data-def="{mean:.1f}" min="{mean / 2:.1f}" max="{mean * 2:.1f}" step="0.1" value="{mean:.1f}"></td></tr>
<tr><td>盈利永续增长</td><td class="n">0.0%</td><td class="n cur" data-for="g"><span data-g></span>%</td><td class="sld"><input type="range" data-in="g" data-def="0" min="-5" max="5" step="0.5" value="0"></td></tr>
<tr><td>留存的年回报率不变</td><td class="n">{rr:.1f}%</td><td class="n cur" data-for="r"><span data-r></span>%</td><td class="sld"><input type="range" data-in="r" data-def="{rr:.1f}" min="{rr / 2:.1f}" max="{rr * 2:.1f}" step="0.1" value="{rr:.1f}"></td></tr>
<tr><td>要求回报（资本成本）</td><td class="n">10.0%</td><td class="n cur" data-for="k"><span data-k></span>%</td><td class="sld"><input type="range" data-in="k" data-def="10" min="5" max="20" step="0.5" value="10"></td></tr>
</table>
<div class="chain">
<div class="step"><b>每年赚 <span data-e></span> 亿</b>{n}年均值，永续</div>
<div class="to">→</div>
<div class="step"><b>留存 <span data-b></span>% 再投资</b>增长 <span data-g></span>% ＝ 留存率 × 留存回报 <span data-r></span>%</div>
<div class="to">→</div>
<div class="step"><b>每年分出 <span data-d></span> 亿</b>其余分给股东</div>
<div class="to">÷</div>
<div class="step"><b>资本成本 <span data-k></span>% − 增长 <span data-g></span>%</b>永续增长折现</div>
<div class="to">＝</div>
<div class="step"><b>现值 · <span data-pv></span> 亿</b>假设下的现金流折现</div>
</div>
<div class="return">留存年回报率 <span data-r></span>% 与 资本成本 <span data-k></span>%：<span data-cmp></span></div>
<div class="nums"><div><dt>假设下的价值参考</dt><dd data-v></dd></div></div>
</div>"""


def gen_capital(agent: Agent, stock: str, market: str, root: Path, force: bool) -> tuple[int, str]:
    derived = root / market / stock / "derived"
    mds = sorted(derived.glob("*.md"))
    if not mds:
        raise SystemExit(f"{derived} 没有年报 md，先跑 ocr/cli.py")

    out_path = root / market / stock / "knowledge" / f"fy{report_year(agent, mds[-1])}" / "capital.json"
    if out_path.exists() and not force:
        logger.info(f"跳过（已存在）: {out_path}")
        art = json.loads(out_path.read_text(encoding="utf-8"))
        return art["year"], art["doc"]

    per_year = {}
    for md_path in mds:
        year = report_year(agent, md_path)
        logger.info(f"{year} …")
        raw = _extract_year(agent, md_path, force)
        raw.pop("dividend_items", None)  # 旧缓存里的分红字段作废，以独立通道为准
        raw["dividend_items"] = _extract_dividend(agent, md_path, force)
        per_year[year] = raw

    doc = build_doc(per_year)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps({"year": max(per_year), "topic": "capital", "source": {"years": sorted(per_year)}, "doc": doc}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    logger.info(f"输出: {out_path}")
    return max(per_year), doc


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S", stream=sys.stderr)
    parser = argparse.ArgumentParser(description="capital 模块：蒸馏资本回报，产物 knowledge/fy<最新年>/capital.json")
    parser.add_argument("--stock", default="601633")
    parser.add_argument("--market", default="cn", choices=["cn", "hk"])
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1] / ".cagent"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    agent = Agent(trace=True)
    gen_capital(agent, args.stock, args.market, Path(args.root), args.force)

    from cagent.wiki_render import render_module

    html_path = render_module(Path(args.root), args.market, args.stock, "capital", Path("output/wiki") / f"{args.market}_{args.stock}" / "capital.html")
    logger.info(f"渲染: {html_path}")


if __name__ == "__main__":
    main()
