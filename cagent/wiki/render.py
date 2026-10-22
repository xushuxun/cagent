"""render：组装 wiki 报告并渲染成带侧边目录的 HTML。

调 business / decisions 生成各节 markdown，渲染成单个 HTML 交付。
mistune 渲染正文；侧边目录由正文里的二、三级标题现算，标题顺序即阅读顺序（正文不带编号）。
页面 JS 只做三件事：剪贴板、目录跳转、当前位置高亮。不内置任何提示词、不下结论。

用法：
    uv run -m cagent.wiki.render --stock 601633 --market cn
"""

import argparse
import json
import logging
import re
import sys
from pathlib import Path

import mistune
from jinja2 import Environment

from cagent.core.agent import Agent
from cagent.wiki import business, decisions

logger = logging.getLogger(__name__)

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


def build(agent: Agent, stock: str, market: str, root: Path, out: Path, force: bool = False) -> Path:
    """生成各节（有产物就复用），渲染成单个 HTML。返回 html 路径。"""
    section_dir = out / market / stock
    docs = [
        business.build(agent, stock, market, root, section_dir, force),
        decisions.build(agent, stock, market, root, section_dir, force),
    ]
    html = render_doc(docs, title=f"{stock} 价值投资档案", ticker=f"{market}:{stock}")
    target = section_dir / "wiki.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html, encoding="utf-8")
    logger.info(f"write {target}")
    return target


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S", stream=sys.stderr)
    parser = argparse.ArgumentParser(description="渲染 wiki 报告")
    parser.add_argument("--stock", default="601633")
    parser.add_argument("--market", default="cn", choices=["cn", "hk"])
    parser.add_argument("--root", type=Path, default=Path(".cagent"))
    parser.add_argument("--out", type=Path, default=Path("output/wiki"))
    parser.add_argument("--force", action="store_true", help="重跑各节（默认复用已有小节）")
    args = parser.parse_args()

    llm = Agent(trace=True)
    build(llm, args.stock, args.market, args.root, args.out, force=args.force)


if __name__ == "__main__":
    main()
