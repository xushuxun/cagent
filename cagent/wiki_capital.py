"""capital 模块：资本回报 writer（重写中）

第一步先做会计报表盘点：对每年年报，由模型列出全部会计报表——只列名录
（照抄标题、所在章节、口径、覆盖期间），不提取表内任何数字。产物缓存到
knowledge/<年报md同名目录>/tables.json；后续的利润与分配 writer 在此基础上实现，
知识产物落位 knowledge/<年报md同名目录>/capital.json。

用法：
    uv run cagent/wiki_capital.py --stock 601633 --market cn
    uv run cagent/wiki_capital.py --stock 601633 --market cn --force
"""

import argparse
import json
import logging
import sys
from pathlib import Path

from cagent.agent import Agent
from cagent.chunk import Toc, report_year

logger = logging.getLogger(__name__)

_tables_pick_task = "你在通读一份上市公司年报的目录。要列出这份年报里的全部会计报表（审计报告后的合并及母公司财务报表、附注中的各张报表），需要阅读哪些章节？"

_tables_prompt = """
你在阅读一份上市公司年报的节选。列出其中出现的全部会计报表，只列名录，不提取表内任何数字：
每张表照抄其标题、所属章节、报表口径（合并/母公司/其他）、覆盖期间（如本期/上期、期末/期初）。
节选里没有表则空数组。
""".strip()

_tables_schema = {
    "type": "object",
    "properties": {
        "tables": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "报表标题，照抄原文"},
                    "chapter": {"type": "string", "description": "所在章节标题"},
                    "scope": {"type": "string", "description": "合并/母公司/其他"},
                    "period": {"type": "string", "description": "覆盖期间，如 本期/上期、期末/期初"},
                },
                "required": ["title", "chapter", "scope", "period"],
            },
        }
    },
    "required": ["tables"],
}


def list_year_tables(agent: Agent, md_path: Path, force: bool = False) -> list[dict]:
    """单年年报的全部会计报表名录（缓存到 knowledge/<md同名目录>/tables.json）。只列名录，不取数。"""
    cache = md_path.parent.parent / "knowledge" / md_path.stem / "tables.json"
    if cache.exists() and not force:
        return json.loads(cache.read_text(encoding="utf-8"))["tables"]
    year = report_year(agent, md_path)
    toc = Toc(agent, md_path)
    picked = toc.pick_chapters(toc.chapters, _tables_pick_task)
    logger.info(f"{year} 报表盘点选中章: {[toc.chapters[i]['title'] for i in picked]}")
    tables = []
    for chunk in (c for i in picked for c in toc.chapter_text_chunk(i)):
        r = agent.chat_json(f"这份年报的报告期是{year}年度。\n{_tables_prompt}\n\n<pages>\n{chunk}\n</pages>", _tables_schema)
        tables.extend(r.get("tables", []))
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"tables": tables}, ensure_ascii=False, indent=1), encoding="utf-8")
    return tables


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S", stream=sys.stderr)
    parser = argparse.ArgumentParser(description="capital 模块：先盘点每年年报的全部会计报表名录")
    parser.add_argument("--stock", default="601633")
    parser.add_argument("--market", default="cn", choices=["cn", "hk"])
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1] / ".cagent"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    agent = Agent(trace=True)
    derived = Path(args.root) / args.market / args.stock / "derived"
    mds = sorted(derived.glob("*.md"))
    if not mds:
        raise SystemExit(f"{derived} 没有年报 md，先跑 ocr/cli.py")

    for md_path in mds:
        tables = list_year_tables(agent, md_path, args.force)
        print(f"\n=== {md_path.name} · {report_year(agent, md_path)} 年度 · {len(tables)} 张表 ===")
        for t in tables:
            print(f"- {t['title']}｜{t['chapter']}｜{t['scope']}｜{t['period']}")


if __name__ == "__main__":
    main()
