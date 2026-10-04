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

TOPICS = [  # 页面骨架：topic → 锚点与栏目名，与内容语义无关
    ("business", "s1", "生意过程简介"),
    ("products", "s2", "主要产品"),
    ("governance", "s3", "治理与激励"),
    ("decisions", "s4", "公司历年经营决策"),
]

TABLE_RE = re.compile(r"<table>.*?</table>", re.DOTALL)


def _sig_and_body(table: str) -> tuple[str | None, str]:
    """拆出表格的首行签名（含 <th> 则为表头签名）与剩余行。无表头时签名 None、全部行入 body。"""
    m = re.match(r"<table>\s*(.*?)</table>", table, re.DOTALL)
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


def render(content: dict) -> str:
    company = content["company"]
    nav, sections = [], []
    for topic, anchor, title in TOPICS:
        arts = content["topics"].get(topic)
        if not arts:
            continue
        nav.append(f'<a href="#{anchor}">{html.escape(title)}</a>')
        body = merge_tables("\n\n".join(str(md(art["doc"])) for art in arts))
        sections.append(f'<h2 id="{anchor}">{html.escape(title)}</h2>\n\n{body}')

    page = TEMPLATE.read_text(encoding="utf-8")
    replacements = {
        "{title}": f'{company["name"]} · {company["latest_year"]} 年年度报告摘要',
        "{topbar}": f'{company["name"]} · {company["latest_year"]} 年年度报告摘要',
        "{ticker}": company["ticker"],
        "{footer}": f'数据：{company["name"]} {company["latest_year"]} 年年度报告（{company["code"]}）合并口径 · 每个数字经过程序核验 · 本页只描述，不评判 · 推算值注明算法',
        "<!--NAV-->": "\n  ".join(nav),
        "<!--SECTIONS-->": "\n\n".join(sections),
    }
    for marker, value in replacements.items():
        assert marker in page, f"模板缺少占位标记: {marker}"
        page = page.replace(marker, value)
    return page


def load_knowledge(root: Path, market: str, stock: str) -> dict:
    """reader：扫描知识层，按 topic 归集各年产物（doc 原样传递，不解析语义）。"""
    knowledge_dir = root / market / stock / "knowledge"
    company = json.loads((knowledge_dir / "company.json").read_text(encoding="utf-8"))
    fys = sorted(int(p.name[2:]) for p in knowledge_dir.glob("fy*") if p.is_dir())
    if not fys:
        raise SystemExit(f"{knowledge_dir} 没有 fy* 目录，先跑 writer")
    topics: dict[str, list[dict]] = {}
    for fy in fys:
        for p in sorted((knowledge_dir / f"fy{fy}").glob("*.json")):
            art = json.loads(p.read_text(encoding="utf-8"))
            topics.setdefault(art["topic"], []).append(art)
    for arts in topics.values():
        arts.sort(key=lambda a: a["fy"])
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
    print(f"输出: {out}（栏目: {[k for k, _a, _t in TOPICS if k in content['topics']]}）")
