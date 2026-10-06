"""trace 可视化：

uv run python -m cagent.trace_web
打开 http://localhost:5000
"""

import json
from datetime import datetime
from pathlib import Path

from flask import Flask, abort, render_template

TRACE_DIR = Path("output/trace")

app = Flask(__name__)


@app.route("/")
def index():
    files = []
    for f in sorted(TRACE_DIR.glob("*.jsonl"), reverse=True):
        st = f.stat()
        with open(f, encoding="utf-8") as fp:
            n = sum(1 for _ in fp)
        files.append(
            {
                "name": f.name,
                "records": n,
                "size": f"{st.st_size / 1024:.0f} KB",
                "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%m-%d %H:%M"),
            }
        )
    return render_template("index.html", files=files)


@app.route("/trace/<name>")
def trace(name):
    f = TRACE_DIR / name
    if not f.is_file() or "/" in name or name.startswith("."):
        abort(404)
    records = [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines()]
    return render_template("trace.html", name=name, records=records)


if __name__ == "__main__":
    app.run(port=5000)
