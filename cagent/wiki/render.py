"""render：Markdown → 带侧边目录的 HTML。纯内存，不写文件。

mistune 渲染正文；侧边目录由正文里的二、三级标题现算，标题顺序即阅读顺序（正文不带编号）。
页面 JS 只做三件事：剪贴板、目录跳转、当前位置高亮。不内置任何提示词、不下结论。

用法：
    html = render_doc([md, …], title="…", ticker="…")
"""

import json
import re
from pathlib import Path

import mistune
from jinja2 import Environment

_TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "wiki.html"

_md = mistune.create_markdown(escape=False, plugins=["table"])
_HEADING = re.compile(r"<h([1-3])>(.*?)</h\1>", re.DOTALL)
_TAG = re.compile(r"<[^>]+>")


def _with_toc(html: str) -> tuple[str, list[dict]]:
    """给二、三级标题打锚点，并顺带生成目录。"""
    toc: list[dict] = []

    def repl(match: re.Match) -> str:
        level, inner = int(match.group(1)), match.group(2)
        anchor = f"sec-{len(toc) + 1}"
        toc.append({"level": level, "text": _TAG.sub("", inner).strip(), "anchor": anchor})
        return f'<h{level} id="{anchor}">{inner}</h{level}>'

    return _HEADING.sub(repl, html), toc


def _html(doc: str) -> str:
    out = _md(doc)
    assert isinstance(out, str)  # mistune 的 HTML renderer 返回 str，类型标注过宽
    return out


def render_doc(docs: list[str], *, title: str, ticker: str = "") -> str:
    tpl = Environment(autoescape=False).from_string(_TEMPLATE.read_text(encoding="utf-8"))
    raw = "\n".join(f'<section class="mod">\n{_html(doc).strip()}\n</section>' for doc in docs)
    body, toc = _with_toc(raw)
    copy_text = "\n\n".join(doc.strip() for doc in docs) + "\n"
    return tpl.render(title=title, ticker=ticker, body=body, toc=toc, copy_json=json.dumps(copy_text, ensure_ascii=False))
