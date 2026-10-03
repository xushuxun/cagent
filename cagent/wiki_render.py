"""wiki 页面渲染：知识层 / content.json + templates/wiki.html → index.html

用法（从知识层组装，推荐）：
    uv run cagent/wiki_render.py --stock 601633 --market cn --out output/wiki/601633/index.html

用法（content.json 直渲，调试）：
    uv run cagent/wiki_render.py --content content.json --out index.html

知识层是唯一事实源：.cagent/<market>/<stock>/knowledge/fy<year>/<topic>.json。
只渲染有产物的栏目，导航动态生成；资本回报的衍生数字为纯算术（compute_derived）。
"""

import argparse
import html
import json
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parent / "templates" / "wiki.html"


def esc(s) -> str:
    return html.escape(str(s), quote=False)


def n1(x: float) -> str:
    """亿元口径：一位小数，整数值去小数。"""
    s = f"{x:.1f}"
    return s.removesuffix(".0")


def compute_derived(capital: dict) -> dict:
    """衍生指标只在数据齐全的年份上计算（None 表示该年提取未通过，展示为 —）。"""
    years = capital["years"]
    complete = [
        (y, np_, d, c, dv)
        for y, np_, d, c, dv in zip(years, capital["net_profit"], capital["d_and_a"], capital["capex"], capital["dividend"])
        if None not in (np_, d, c, dv)
    ]
    assert complete, "资本回报没有一年数据齐全"
    oe = [p + d - c for _, p, d, c, _ in complete]
    total_profit, total_div = sum(p for _, p, _, _, _ in complete), sum(dv for _, _, _, _, dv in complete)
    total_retained = total_profit - total_div
    profit_growth = complete[-1][1] - complete[0][1]
    incremental_return = profit_growth / total_retained
    return {
        "owner_earnings_by_year": {y: p + d - c for y, p, d, c, _ in complete},
        "retained_by_year": {y: p - dv for y, p, _, _, dv in complete},
        "total_profit": total_profit,
        "total_dividend": total_div,
        "total_retained": total_retained,
        "profit_growth": profit_growth,
        "incremental_return": incremental_return,
        "avg_oe": sum(oe) / len(oe),
        "oe_total": sum(oe),
        "oe_pct": sum(oe) / total_profit * 100,
        "div_pct": total_div / total_profit * 100,
        "per_retained": profit_growth / total_retained,
        "n_years": len(complete),
        "first_year": complete[0][0],
        "last_year": complete[-1][0],
    }


def frag_s1(business: dict) -> str:
    paras = "\n".join(f"<p>{esc(p)}</p>" for p in business["description"].split("\n\n") if p.strip())
    process = business.get("process") or []
    if not process:
        return f"""<h2 id="s1">生意过程简介</h2>

<h3 id="s1-1">业务说明</h3>
<div class="prose">
{paras}
</div>"""
    steps = []
    for i, s in enumerate(process):
        if i:
            steps.append('<div class="to">→</div>')
        steps.append(f'<div class="step"><b>{esc(s["step"])}</b>{esc(s["detail"])}</div>')
    return_note = business.get("return_note", "")
    return_line = f'<div class="return">↩ {esc(return_note)}</div>' if return_note else ""
    return f"""<h2 id="s1">生意过程简介</h2>

<h3 id="s1-1">业务说明</h3>
<div class="prose">
{paras}
</div>

<h3 id="s1-2">经营过程</h3>
<div class="chain">
  {' '.join(steps)}
</div>
{return_line}"""


def frag_s2(products: list[dict]) -> str:
    def scale_cls(scale: str) -> str:
        return "n" if any(ch.isdigit() for ch in scale) else "src"

    rows = "\n".join(
        f'  <tr><td>{esc(p["name"])}</td><td class="{scale_cls(p["scale"])}">{esc(p["scale"])}</td><td class="src">{esc(p["yoy"])}</td><td class="q">{esc(p["note"])}</td></tr>'
        for p in products
    )
    return f"""<h2 id="s2">主要产品</h2>
<table>
  <tr><th>品牌</th><th>销量</th><th>同比</th><th>定位</th></tr>
{rows}
</table>"""


def frag_s3(org: dict) -> str:
    tiers = []
    for i, tier in enumerate(org["tree"]):
        if i:
            tiers.append('  <div class="conn">│</div>')
        cards = " ".join(
            f'<div class="card{" top" if c.get("top") else ""}"><b>{esc(c["title"])}</b>{esc(c["body"])}</div>'
            for c in tier["cards"]
        )
        tiers.append(f'  <div class="tier">{cards}</div>')
    exec_rows = "\n".join(
        f'  <tr><td>{esc(e["name"])}</td><td>{esc(e["role"])}</td><td class="q">{esc(e["duty"])}</td><td class="n">{esc(e["pay"])}</td></tr>'
        for e in org["executives"]
    )
    emp_rows = "\n".join(
        f'  <tr{" class=\"hl\"" if r.get("hl") else ""}><td>{esc(r["item"])}</td><td class="n">{esc(r["value"])}</td><td class="src">{esc(r["src"])}</td></tr>'
        for r in org["employees"]
    )
    notes = f'<div class="note">{esc(org["org_note"])}</div>' if org.get("org_note") else ""
    exec_note = f'<div class="note">{esc(org["exec_note"])}</div>' if org.get("exec_note") else ""
    return f"""<h2 id="s3">公司人事组织架构</h2>

<h3 id="s3-1">组织架构</h3>
<div class="tree">
{chr(10).join(tiers)}
</div>

{notes}
<h3 id="s3-3">高级管理人员</h3>
<table>
  <tr><th>姓名</th><th>职务</th><th>分管</th><th>税前薪酬（万元）</th></tr>
{exec_rows}
</table>

{exec_note}
<h3 id="s3-4">员工构成</h3>
<table>
  <tr><th>类别</th><th>人数</th><th>占比</th></tr>
{emp_rows}
</table>"""


def frag_s4(decisions: list[dict], note: str = "") -> str:
    def join(v):
        if not isinstance(v, list):
            return str(v)
        return "；".join(str(x).rstrip("。") for x in v) + "。"

    rows = "\n".join(
        f'  <tr><td class="n">{d.get("year", d.get("fy"))}</td><td class="q">{esc(join(d["judgment"]))}</td><td>{esc(join(d["actions"]))}</td></tr>'
        for d in decisions
    )
    note_html = f'<div class="note">{esc(note)}</div>' if note else ""
    return f"""<h2 id="s4">公司历年经营决策</h2>

<table class="stats">
  <tr><th>年份</th><th>管理层对行业大环境的判断</th><th>当年的重大经营决策</th></tr>
{rows}
</table>

{note_html}"""


SECTIONS = [  # (content_key, nav_item, subs)
    ("business", '<a href="#s1">生意过程简介</a>', ['<a class="sub" href="#s1-1">业务说明</a>']),
    ("products", '<a href="#s2">主要产品</a>', []),
    ("org", '<a href="#s3">公司人事组织架构</a>', []),
    ("decisions", '<a href="#s4">公司历年经营决策</a>', []),
    ("capital", '<a href="#s5">资本回报</a>', ['<a class="sub" href="#s5-1">历年总览</a>', '<a class="sub" href="#s5-2">资本配置</a>', '<a class="sub" href="#s5-3">综合判断</a>', '<a class="sub" href="#s5-4">估值试算</a>']),
]


def render(content: dict) -> str:
    company = content["company"]
    has = {k: k in content and content[k] for k, _n, _s in SECTIONS}
    nav = "\n  ".join(item + ("\n  " + "\n  ".join(subs) if has[k] and subs else "") for k, item, subs in SECTIONS if has[k])

    frags = {}
    if has["business"]:
        frags["<!--S1-->"] = frag_s1(content["business"])
    if has["products"]:
        frags["<!--S2-->"] = frag_s2(content["products"])
    if has["org"]:
        frags["<!--S3-->"] = frag_s3(content["org"])
    if has["decisions"]:
        frags["<!--S4-->"] = frag_s4(content["decisions"], content.get("decisions_note", ""))
    if has["capital"]:
        frags["<!--S5-->"] = frag_s5(content["capital"], compute_derived(content["capital"]), content["capital"]["years"])

    page = TEMPLATE.read_text(encoding="utf-8")
    replacements = {
        "{title}": f'{company["name"]} · {company["latest_year"]} 年年度报告摘要',
        "{topbar}": f'{company["name"]} · {company["latest_year"]} 年年度报告摘要',
        "{ticker}": company["ticker"],
        "{footer}": f'数据：{company["name"]} {company["latest_year"]} 年年度报告（{company["code"]}）合并口径 · 每个数字经过程序核验 · 本页只描述，不评判 · 推算值注明算法',
        "{oe}": "0",
        "{r}": "0",
        "<!--NAV-->": nav,
        **frags,
    }
    if has["capital"]:
        d = compute_derived(content["capital"])
        replacements["{oe}"] = f"{d['avg_oe']:.4f}"
        replacements["{r}"] = f"{d['incremental_return']:.6f}"
    for marker, value in replacements.items():
        assert marker in page, f"模板缺少占位标记: {marker}"
        page = page.replace(marker, value)
    for marker in ("<!--S1-->", "<!--S2-->", "<!--S3-->", "<!--S4-->", "<!--S5-->"):
        page = page.replace(marker, "")  # 无产物的栏目整块缺席
    return page


def load_knowledge(root: Path, market: str, stock: str) -> dict:
    """reader：扫描知识层，组装 content。只收蒸馏描述与定位索引，不碰原文。"""
    knowledge_dir = root / market / stock / "knowledge"
    company = json.loads((knowledge_dir / "company.json").read_text(encoding="utf-8"))
    fys = sorted(int(p.name[2:]) for p in knowledge_dir.glob("fy*") if p.is_dir())
    if not fys:
        raise SystemExit(f"{knowledge_dir} 没有 fy* 目录，先跑 writer")
    content: dict = {"company": {**company, "latest_year": fys[-1]}}

    def read(fy: int, topic: str) -> dict | None:
        p = knowledge_dir / f"fy{fy}" / f"{topic}.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    business = read(fys[-1], "business")
    if business:
        content["business"] = {"description": business["description"]}

    rows = [d for fy in fys if (d := read(fy, "decisions"))]
    if rows:
        content["decisions"] = rows
        content["decisions_note"] = "判断写在年报的管理层讨论里，决策记在报表的科目里——下一栏资本回报，是这些年决策的成绩单。"
    return content


def frag_s5(capital: dict, d: dict, years: list[int]) -> str:
    year_heads = "".join(f"<th>{y}</th>" for y in years)
    span = f"{d['first_year']}–{d['last_year']}"
    n_years = d["n_years"]
    cn_n = {2: "两", 3: "三", 4: "四", 5: "五", 6: "六"}.get(n_years, str(n_years))

    def cells(values):
        def one(v, y):
            if v is None:
                return "<td>—</td>"
            return f'<td class="n">{n1(v)}</td>'
        return "".join(one(v, y) for v, y in zip(values, years))

    overview_rows = [
        ('<tr class="hl"><td>归母净利润</td>', capital["net_profit"]),
        ("<tr><td>折旧与摊销</td>", capital["d_and_a"]),
        ("<tr><td>购建长期资产付现</td>", capital["capex"]),
        ("<tr><td>所有者收益</td>", [d["owner_earnings_by_year"].get(y) for y in years]),
        ("<tr><td>分红付现</td>", capital["dividend"]),
        ("<tr><td>留存利润</td>", [d["retained_by_year"].get(y) for y in years]),
        ("<tr><td>经营现金流净额</td>", capital["cfo"]),
    ]
    overview = "\n".join(f"{head}{cells(v)}</tr>" for head, v in overview_rows)
    overview_note = f'<div class="note">{esc(capital["overview_note"])}</div>' if capital.get("overview_note") else ""

    ir = d["incremental_return"]
    ir_pct = f"{ir * 100:.1f}"
    ir_round = round(ir * 100)
    pr = f"{d['per_retained']:.2f}"
    gap = round((ir - 0.10) * 100)
    gap_sentence = (
        f"高出及格线约 {gap} 个百分点" if gap > 0 else f"低于及格线约 {-gap} 个百分点" if gap < 0 else "正好压在及格线上"
    )
    vpr = f"{ir - 0.10:.2f}"
    compound_sentence = (
        f"留存在创造复利，但每 1 元留存每年只多创造约 {vpr} 元的价值，增长的成色平庸"
        if ir >= 0.10
        else f"留存每年毁损约 {vpr[1:]} 元的价值，增长越多亏得越多"
    )

    return f"""<h2 id="s5">资本回报</h2>

<h3 id="s5-1">历年总览</h3>
<table class="stats">
  <tr><th>指标（亿元）</th>{year_heads}</tr>
{overview}
</table>

{overview_note}
<h3 id="s5-2">资本配置</h3>
<div class="tree">
  <div class="tier"><div class="card top"><b>所有者收益</b>归母净利润 ＋ 折旧摊销 − 维持生意所需的资本开支</div></div>
  <div class="conn">│</div>
  <div class="tier">
    <div class="card"><b>分红</b>交给股东的部分，累计 {n1(d["total_dividend"])} 亿</div>
    <div class="card"><b>留存</b>留在生意里的部分，累计 {n1(d["total_retained"])} 亿</div>
  </div>
  <div class="conn">│</div>
  <div class="tier"><div class="card top"><b>留存的回报</b>累计净利润增长 {n1(d["profit_growth"])} 亿，每 1 元留存每年赚回 {pr} 元</div></div>
</div>
<div class="return">分红是落袋的回报，留存是继续复利的本金——复利的成色，看每 1 元留存每年赚回多少：按 10% 资本成本，及格线是 0.10 元</div>

<table>
  <tr><th>累计（{span}）</th><th>金额</th><th>说明</th></tr>
  <tr><td>归母净利润</td><td class="n">{n1(d["total_profit"])} 亿</td><td class="src">各年合计</td></tr>
  <tr><td>分红</td><td class="n">{n1(d["total_dividend"])} 亿</td><td class="src">占净利润 {d["div_pct"]:.1f}%</td></tr>
  <tr><td>留存</td><td class="n">{n1(d["total_retained"])} 亿</td><td class="src">净利润减去分红</td></tr>
  <tr><td>净利润增长</td><td class="n">{n1(d["profit_growth"])} 亿</td><td class="src">{d["last_year"]} 年较 {d["first_year"]} 年</td></tr>
</table>

<div class="note">逐年检验容易被单年波动误导。累计看：留存 {n1(d["total_retained"])} 亿，换回每年多赚 {n1(d["profit_growth"])} 亿，每 1 元留存每年赚回 {pr} 元——刚过 10% 的资本成本及格线；若资本成本为 12%，同样的数字从及格转为毁损。这个回报能否守住，取决于生意的护城河。</div>

<h3 id="s5-3">综合判断</h3>
<table class="stats">
  <tr><th>累计（{span}）</th><th>金额</th><th>含义</th></tr>
  <tr><td>所有者收益</td><td class="n">{n1(d["oe_total"])} 亿</td><td class="src">{cn_n} 年赚到的现金，占净利润 {d["oe_pct"]:.0f}%</td></tr>
  <tr class="hl"><td>留存</td><td class="n">{n1(d["total_retained"])} 亿</td><td class="src">留在生意里的股东资本</td></tr>
  <tr class="hl"><td>每年多赚</td><td class="n">{n1(d["profit_growth"])} 亿</td><td class="src">留存换来的增量利润</td></tr>
  <tr class="hl"><td>资本回报率</td><td class="n">约 {ir_round}%</td><td class="src">每年多赚 ÷ 留存</td></tr>
</table>

<div class="return">资本成本 10% —— 每 1 元留存每年赚回 {pr} 元，{gap_sentence}</div>

<div class="note">{span}累计所有者收益 {n1(d["oe_total"])} 亿，占净利润的 {d["oe_pct"]:.0f}%——利润有现金支撑，不是账面数字。留存 {n1(d["total_retained"])} 亿换回每年多赚 {n1(d["profit_growth"])} 亿，回报率约 {ir_round}%：{compound_sentence}。这个回报能否守住，取决于护城河，而不是取决于这{cn_n}年的算术。</div>

<h3 id="s5-4">估值试算</h3>
<div class="calc-row">
  <span>资本成本</span>
  <input type="range" id="k" min="6" max="15" step="0.5" value="10">
  <b id="kv">10.0%</b>
</div>
<div class="nums">
  <div><dt>这门生意值多少</dt><dd id="vBase">—</dd></div>
</div>
<div class="return" id="vVerdict"></div>
<div class="note">这门生意值多少 ＝ 年均所有者收益 {n1(d["avg_oe"])} 亿 ÷ 资本成本。资本成本由你定，其余数字都是这门生意已经交出的成绩：所有者收益来自前表，增量回报 {ir_pct}% 与资本成本一比，方向立判。</div>"""


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="渲染 wiki 页面")
    parser.add_argument("--content", help="content.json 路径（不给则从知识层组装）")
    parser.add_argument("--stock", default="601633")
    parser.add_argument("--market", default="cn", choices=["cn", "hk"])
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1] / ".cagent"))
    parser.add_argument("--out", required=True, help="输出 index.html 路径")
    args = parser.parse_args()

    if args.content:
        content = json.loads(Path(args.content).read_text(encoding="utf-8"))
    else:
        content = load_knowledge(Path(args.root), args.market, args.stock)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(content), encoding="utf-8")
    print(f"输出: {out}（栏目: {[k for k, _n, _s in SECTIONS if k in content]}）")
