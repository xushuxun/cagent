"""wiki render：Markdown doc → wiki HTML 文本。纯内存，不写文件。

markdown 由页面 CDN 引入的 marked 渲染，这里只做模板填充。

用法：
    html = render_doc(doc, title="…", ticker="…")
"""

from pathlib import Path

from jinja2 import Template

_TEMPLATE = Path(__file__).parent / "templates" / "wiki.html"


def render_doc(doc: str, *, title: str, ticker: str = "", topbar: str | None = None, footer: str = "") -> str:
    tpl = Template(_TEMPLATE.read_text(encoding="utf-8"))
    return tpl.render(title=title, topbar=topbar or title, ticker=ticker, doc=doc, footer=footer)
