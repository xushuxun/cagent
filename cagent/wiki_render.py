"""wiki render：Markdown doc → wiki HTML 文本。纯内存，不写文件。

markdown 由页面 CDN 引入的 marked 渲染，这里只做模板填充。
渲染与 agent 产物的唯一耦合点是模块：按模块大标题选择版式，不解析内容。

用法：
    html = render_doc(doc, title="… · 产品与销量", ticker="…")
"""

from pathlib import Path

from jinja2 import Template

_TEMPLATE = Path(__file__).parent / "templates" / "wiki.html"

# 模块大标题（含即可）→ UI 版式；未命中或未列出的标题按默认版式正常渲染 markdown
_LAYOUTS = {
    "生意模式": "default",
    "产品与销量": "columns",
    "历年经营决策": "default",
}


def render_doc(doc: str, *, title: str, ticker: str = "", topbar: str | None = None, footer: str = "") -> str:
    tpl = Template(_TEMPLATE.read_text(encoding="utf-8"))
    layout = next((v for k, v in _LAYOUTS.items() if k in title), "")
    return tpl.render(title=title, topbar=topbar or title, ticker=ticker, doc=doc, footer=footer, layout=layout)
