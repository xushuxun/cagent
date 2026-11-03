"""把三表 JSON 渲染为单页 HTML：uv run python -m cagent.statements.render -i <stem.statements>"""

import argparse
import json
from decimal import Decimal
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from cagent.core.storage import atomic_write
from cagent.statements.check import statement_paths

_KINDS = ("balance_sheet", "income_statement", "cash_flow")
_TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "templates"


def _amount(value: float | None) -> str:
    return "—" if value is None else f"{Decimal(str(value)):,}"


def render_dir(statements_dir: Path, output: Path) -> Path:
    meta = json.loads((statements_dir / "meta.json").read_text(encoding="utf-8"))
    statements = []
    for kind in _KINDS:
        statement = json.loads((statements_dir / f"{kind}.json").read_text(encoding="utf-8"))
        for row, path in zip(statement["rows"], statement_paths(statement["rows"]), strict=True):
            row["depth"] = len(path) - 1
        statements.append(statement)
    env = Environment(loader=FileSystemLoader(_TEMPLATE_DIR), autoescape=select_autoescape())
    html = env.get_template("statements.html").render(meta=meta, statements=statements, amount=_amount)
    output.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(output, html.encode("utf-8"))
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="渲染合并三大主表")
    parser.add_argument("-i", "--input", type=Path, required=True)
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args()
    output = args.output or Path("output/report") / args.input.parent.parent.name / f"{args.input.stem}.html"
    print(render_dir(args.input, output))
