"""wiki render：Markdown doc → wiki HTML 文本。纯内存，不写文件。

Markdown 由 mistune 渲染；页面 JS 只有剪贴板。每个 doc 自带一级标题。
复制按钮只复制报告全文：产品不下结论，估值判断留给读者，不内置任何提示词。

用法：
    html = render_doc([md, …], title="…", ticker="…")
"""

import json
from pathlib import Path

import mistune
from jinja2 import Environment

_TEMPLATE = Path(__file__).parent / "templates" / "wiki.html"

_md = mistune.create_markdown(escape=False, plugins=["table"])


def _html(doc: str) -> str:
    out = _md(doc)
    assert isinstance(out, str)  # mistune 的 HTML renderer 返回 str，类型标注过宽
    return out


def render_doc(docs: list[str], *, title: str, ticker: str = "") -> str:
    tpl = Environment(autoescape=False).from_string(_TEMPLATE.read_text(encoding="utf-8"))
    body = "\n".join(f'<section class="mod">\n{_html(doc).strip()}\n</section>' for doc in docs)
    copy_text = "\n\n".join(doc.strip() for doc in docs) + "\n"
    return tpl.render(title=title, ticker=ticker, body=body, copy_json=json.dumps(copy_text, ensure_ascii=False))
