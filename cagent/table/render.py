"""把 derived/<stem>.statements/ 渲染为单页静态 HTML：

    uv run python -m cagent.table.render -i .cagent/cn/002594/derived/2025-03-25_1222881496.statements
"""

import argparse
import json
from decimal import Decimal
from pathlib import Path

import mistune
from jinja2 import Environment, FileSystemLoader, select_autoescape

from cagent.core.storage import atomic_write

_TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "templates"
_markdown = mistune.create_markdown(escape=False, plugins=["table"])


def _amount(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{Decimal(str(value)):,}"


def render_dir(statements_dir: Path, output: Path) -> Path:
    meta = json.loads((statements_dir / "meta.json").read_text(encoding="utf-8"))
    statements = [json.loads((statements_dir / f"{kind}.json").read_text(encoding="utf-8")) for kind in ("bs", "is", "cf")]
    context_path = statements_dir / "context.json"
    context: dict = json.loads(context_path.read_text(encoding="utf-8")) if context_path.exists() else {"notes": [], "rows": {}}
    entries = {entry["id"]: {**entry, "content": _markdown(entry["content"])} for entry in context["notes"]}
    row_links: dict[str, dict] = context["rows"]
    for kind, statement in zip(("bs", "is", "cf"), statements, strict=True):
        for index, row in enumerate(statement["rows"]):
            links = row_links.get(f"{kind}:{index}", {})
            row["notes"] = [entries[entry_id] for entry_id in links.get("notes", [])]
    env = Environment(loader=FileSystemLoader(_TEMPLATE_DIR), autoescape=select_autoescape())
    html = env.get_template("statements.html").render(meta=meta, statements=statements, amount=_amount)
    output.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(output, html.encode("utf-8"))
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="渲染合并三大主表")
    parser.add_argument("-i", "--input", type=Path, required=True, help="statements 目录")
    parser.add_argument("-o", "--output", type=Path, help="HTML 输出路径")
    args = parser.parse_args()
    output = args.output or Path("output/report") / args.input.parent.parent.name / f"{args.input.stem}.html"
    print(render_dir(args.input, output))
