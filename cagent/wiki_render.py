"""wiki render：Markdown doc → wiki HTML 文本。纯内存，不写文件。

Markdown 由 mistune 渲染；页面 JS 只有剪贴板。每个 doc 自带一级标题。

用法：
    html = render_doc([md, …], title="…", ticker="…")
"""

import json
from pathlib import Path

import mistune
from jinja2 import Environment

_TEMPLATE = Path(__file__).parent / "templates" / "wiki.html"

VALUATION_PROMPT = (
    "以下是一家中国上市公司的研究报告全文（历年年报合并口径，单位：亿元人民币），只陈列数据，不含估值结论。\n"
    "请用巴菲特、芒格、李录的价值投资框架为其估值：取正常化利润并说明口径与周期位置；按 10% 左右资本成本折算；"
    "加净现金类资产（请依年报资产负债表自行计算）；只有增量资本回报持续高于资本成本时才为成长付费。"
    "给出区间而非单点，估值是假设下的推演，不是结论。\n"
    "—— 报告全文 ——"
)

_md = mistune.create_markdown(escape=False, plugins=["table"])


def _html(doc: str) -> str:
    out = _md(doc)
    assert isinstance(out, str)  # mistune 的 HTML renderer 返回 str，类型标注过宽
    return out


def render_doc(docs: list[str], *, title: str, ticker: str = "") -> str:
    tpl = Environment(autoescape=False).from_string(_TEMPLATE.read_text(encoding="utf-8"))
    body = "\n".join(f'<section class="mod">\n{_html(doc).strip()}\n</section>' for doc in docs)
    copy_text = VALUATION_PROMPT + "\n\n" + "\n\n".join(doc.strip() for doc in docs) + "\n"
    return tpl.render(title=title, ticker=ticker, body=body, copy_json=json.dumps(copy_text, ensure_ascii=False))
