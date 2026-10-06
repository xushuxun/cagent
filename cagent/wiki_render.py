"""wiki 页面渲染：知识层 Markdown 文档 → index.html

renderer 只做格式转换与页面骨架：doc 以 Markdown 为主、内嵌原生 HTML 表格，
各年文档拼接后按通用规则合并相邻的同签名表格（如各年一张的经营决策表）。
不知道"销量""薪酬"是什么；语义组织全部在 writer 侧完成。

用法（从知识层组装，推荐）：
    uv run cagent/wiki_render.py --stock 601633 --market cn --out output/wiki/601633/index.html

用法（content.json 直渲，调试）：
    uv run cagent/wiki_render.py --content content.json --out index.html
"""

import argparse
import html
import json
import re
from pathlib import Path

import mistune

TEMPLATE = Path(__file__).resolve().parent / "templates" / "wiki.html"

md = mistune.create_markdown(escape=False)

TOPICS = [  # 页面骨架：topic → (锚点, 栏目名, 布局提示, 栏目注记)。layout="years" 时各年产物分栏展示，与内容语义无关
    ("business", "s1", "生意模式", None, None),
    ("products", "s2", "产品与销量", "years", "单位：万辆。"),
    ("governance", "s3", "治理与激励", None, None),
    ("decisions", "s4", "公司历年经营决策", None, "判断摘自年报管理层讨论与分析，决策对应报表科目。"),
    ("capital", "s5", "资本回报", None, None),
]

TABLE_RE = re.compile(r"<table[^>]*>.*?</table>", re.DOTALL)
H3_RE = re.compile(r"<h3>(.*?)</h3>")


def _sig_and_body(table: str) -> tuple[str | None, str]:
    """拆出表格的首行签名（含 <th> 则为表头签名）与剩余行。无表头时签名 None、全部行入 body。"""
    m = re.match(r"<table[^>]*>\s*(.*?)</table>", table, re.DOTALL)
    assert m is not None
    inner = m.group(1)
    row = re.match(r"\s*<tr>.*?</tr>", inner, re.DOTALL)
    assert row is not None
    first, rest = row.group(), inner[row.end() :]
    if "<th" in first:
        return first, rest
    return None, first + rest


def merge_tables(doc_html: str) -> str:
    """通用表格合并：相邻（仅空白相隔）的表格，表头签名相同则并行，无表头则并入前一个表。"""
    out: list[str] = []  # 已就位的片段；open_idx 指向可并入的 table 下标
    open_idx, open_sig = -1, None
    pos = 0
    for m in TABLE_RE.finditer(doc_html):
        gap = doc_html[pos : m.start()]
        out.append(gap)
        if gap.strip():
            open_idx, open_sig = -1, None
        sig, body = _sig_and_body(m.group())
        if sig is not None and sig == open_sig and open_idx >= 0:
            out[open_idx] = out[open_idx][: -len("</table>")] + body + "</table>"
        elif sig is not None:
            out.append(m.group())
            open_idx, open_sig = len(out) - 1, sig
        elif open_idx >= 0:
            out[open_idx] = out[open_idx][: -len("</table>")] + body + "</table>"
        else:
            out.append(m.group())
            open_idx, open_sig = len(out) - 1, None
        pos = m.end()
    out.append(doc_html[pos:])
    return "".join(out)


def _anchor_h3(body: str, anchor: str) -> tuple[str, list[str]]:
    """栏目内第 k 个 h3 编为 anchor-k 并生成子导航链接。纯编号，不涉及内容语义。"""
    subs: list[str] = []

    def repl(m: re.Match) -> str:
        subs.append(m.group(1))
        return f'<h3 id="{anchor}-{len(subs)}">{m.group(1)}</h3>'

    body = H3_RE.sub(repl, body)
    return body, [f'<a class="sub" href="#{anchor}-{i}">{t}</a>' for i, t in enumerate(subs, 1)]


def _note_div(note: str | None) -> str:
    return f'\n<div class="note">{html.escape(note)}</div>' if note else ""


def _fill(title: str, ticker: str, footer: str, nav: list[str], sections: list[str]) -> str:
    """套页面骨架：模板占位符替换。"""
    page = TEMPLATE.read_text(encoding="utf-8")
    replacements = {
        "{title}": title,
        "{topbar}": title,
        "{ticker}": ticker,
        "{footer}": footer,
        "<!--NAV-->": "\n  ".join(nav),
        "<!--SECTIONS-->": "\n\n".join(sections),
    }
    for marker, value in replacements.items():
        assert marker in page, f"模板缺少占位标记: {marker}"
        page = page.replace(marker, value)
    return page


def render_docs(heading_docs: list[tuple[str, str]], title: str, ticker: str = "", footer: str = "") -> str:
    """独立渲染：若干 (栏目名, Markdown doc) → 完整 HTML 页面。单个模块不依赖其它产物即可出页。"""
    nav, sections = [], []
    for n, (heading, doc) in enumerate(heading_docs, 1):
        anchor = f"s{n}"
        body, subs = _anchor_h3(merge_tables(str(md(doc)).strip()), anchor)
        nav.append(f'<a href="#{anchor}">{html.escape(heading)}</a>')
        nav.extend(subs)
        sections.append(f'<h2 id="{anchor}">{html.escape(heading)}</h2>\n\n{body}')
    return _fill(title, ticker, footer, nav, sections)


def render_module(root: Path, market: str, stock: str, topic: str, out: Path) -> Path:
    """读知识层某模块全部年份产物，独立渲染成一个 HTML。"""
    knowledge_dir = root / market / stock / "knowledge"
    arts = [
        json.loads(p.read_text(encoding="utf-8"))
        for fy in sorted(p.name for p in knowledge_dir.glob("fy*") if p.is_dir())
        if (p := knowledge_dir / fy / f"{topic}.json").exists()
    ]
    if not arts:
        raise SystemExit(f"{knowledge_dir} 没有 {topic}.json，先跑 writer")
    if _topic_layout(topic) == "years":
        html_text = _fill(f"{stock} · {topic}", "", "", [], [_years_layout(arts, _topic_note(topic))])
    else:
        html_text = render_docs([(str(art["year"]), art["doc"]) for art in arts], title=f"{stock} · {topic}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html_text, encoding="utf-8")
    return out


def _topic_layout(topic: str) -> str | None:
    return next((layout for t, _a, _title, layout, _n in TOPICS if t == topic), None)


def _topic_note(topic: str) -> str | None:
    return next((note for t, _a, _title, _l, note in TOPICS if t == topic), None)


def _years_layout(arts: list[dict], note: str | None = None) -> str:
    cols = "".join(f'<div class="year"><b>{a["year"]}</b>\n{merge_tables(str(md(a["doc"])).strip())}</div>' for a in arts)
    return f'<div class="years">{cols}</div>{_note_div(note)}'


def render(content: dict) -> str:
    company = content["company"]
    nav, sections = [], []
    for topic, anchor, title, layout, note in TOPICS:
        arts = content["topics"].get(topic)
        if not arts:
            continue
        if layout == "years":
            body, subs = _years_layout(arts, note), []
        else:
            body, subs = _anchor_h3(merge_tables("\n\n".join(str(md(art["doc"])).strip() for art in arts)), anchor)
            body += _note_div(note)
        nav.append(f'<a href="#{anchor}">{html.escape(title)}</a>')
        nav.extend(subs)
        sections.append(f'<h2 id="{anchor}">{html.escape(title)}</h2>\n\n{body}')

    name, latest = company["name"], company["latest_year"]
    return _fill(
        title=f"{name} · {latest} 年年度报告摘要",
        ticker=company["ticker"],
        footer=f"数据：{name} {latest} 年年度报告（{company['code']}）合并口径 · 每个数字经过程序核验 · 本页只描述，不评判 · 推算值注明算法",
        nav=nav,
        sections=sections,
    )


def load_knowledge(root: Path, market: str, stock: str) -> dict:
    """reader：扫描知识层，按 topic 归集各年产物（doc 原样传递，不解析语义）。"""
    knowledge_dir = root / market / stock / "knowledge"
    company_path = knowledge_dir / "company.json"
    if company_path.exists():
        company = json.loads(company_path.read_text(encoding="utf-8"))
    else:
        idx = json.loads((root / market / stock / "index.json").read_text(encoding="utf-8"))
        company = {"name": idx["stockName"], "ticker": stock, "code": idx["stockCode"]}
    fys = sorted(int(p.name[2:]) for p in knowledge_dir.glob("fy*") if p.is_dir())
    if not fys:
        raise SystemExit(f"{knowledge_dir} 没有 fy* 目录，先跑 writer")
    topics: dict[str, list[dict]] = {}
    for fy in fys:
        for p in sorted((knowledge_dir / f"fy{fy}").glob("*.json")):
            art = json.loads(p.read_text(encoding="utf-8"))
            if "topic" not in art:
                continue
            topics.setdefault(art["topic"], []).append(art)
    for arts in topics.values():
        arts.sort(key=lambda a: a["year"])
    return {"company": {**company, "latest_year": fys[-1]}, "topics": topics}


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
    print(f"输出: {out}（栏目: {[t[0] for t in TOPICS if t[0] in content['topics']]}）")
