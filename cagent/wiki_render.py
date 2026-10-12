"""wiki render：Markdown doc → wiki HTML 文本。纯内存，不写文件。

markdown 由页面 CDN 引入的 marked 渲染，这里只做模板填充与样式选择（layout），
不认识任何模块；可用版式见 templates/wiki.html 的 body class 分支。

用法：
    html = render_doc(doc, title="…", ticker="…")                        # 默认版式
    html = render_doc(doc, title="…", ticker="…", layout="columns")      # 年份并排分栏
"""

from pathlib import Path

from jinja2 import Template

_TEMPLATE = Path(__file__).parent / "templates" / "wiki.html"


def render_doc(doc: str, *, title: str, ticker: str = "", layout: str = "") -> str:
    tpl = Template(_TEMPLATE.read_text(encoding="utf-8"))
    return tpl.render(title=title, ticker=ticker, doc=doc, layout=layout)
