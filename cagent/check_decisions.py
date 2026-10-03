"""decisions 模块覆盖检查：章节重要内容 vs 知识层已蒸馏内容，报告遗漏

用法：
    uv run cagent/check_decisions.py --stock 601633 --market cn

两段式：逐 chunk 列出章节里所有重要判断/决策候选，再与 knowledge 里已蒸馏的
judgment/actions 比对，只报真正丢失的（意思覆盖到的不算）。报告写到
output/wiki/<stock>/checks/decisions_coverage.json，并打印逐年遗漏数。
"""

import argparse
import json
import logging
import sys
from pathlib import Path

from cagent.agent import Agent, chunk_text
from cagent.chunk import Toc, load_tocs

logger = logging.getLogger(__name__)

llm = Agent(trace=True)

SCHEMA_ITEMS = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"kind": {"type": "string", "enum": ["judgment", "actions"]}, "item": {"type": "string"}},
                "required": ["kind", "item"],
            },
        }
    },
    "required": ["items"],
}

SCHEMA_MISSING = {
    "type": "object",
    "properties": {
        "missing": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"kind": {"type": "string"}, "item": {"type": "string"}},
                "required": ["kind", "item"],
            },
        }
    },
    "required": ["missing"],
}


def chat(prompt: str, schema: dict) -> dict:
    return llm.chat_json(prompt, schema)


def check_year(year: int, toc: Toc, distilled: dict) -> dict:
    # 章节按知识层记录的确切标题定位（索引查找，非关键字匹配）
    text = ""
    for s in distilled["source"]["chapters"]:
        i = next((i for i, c in enumerate(toc.chapters) if c["title"] == s["chapter"]), None)
        if i is None:
            raise ValueError(f"fy{year} 找不到章节: {s['chapter']}")
        text += toc.chapter_text(i)
    candidates = []
    for chunk in chunk_text(text):
        out = chat(
            f"""列出 <pages>（年报管理层讨论）里所有重要的：管理层对行业大环境的判断（kind=judgment）与当年的重大经营决策/事件（kind=actions）。
每条一句话、不超过30字。范围：战略、品牌矩阵调整、产能基地、投资并购、资本动作、组织变革、重大风险；持续推进的长期战略只列当年有新进展的。
不列常规财务数据，不列单一车型与零部件细节（归产品模块）。
<pages>
{chunk}
</pages>""",
            SCHEMA_ITEMS,
        )
        candidates += [f"[{x['kind']}] {x['item']}" for x in out["items"]]
    out = chat(
        f"""下方 <candidates> 是从一份年报管理层讨论中列出的重要判断与决策候选。
<covered> 是已蒸馏进知识层的内容。找出 candidates 里重要但 covered 没有体现的内容（missing）。
covered 允许合并表述，意思覆盖到就不算遗漏；只报真正丢失的重要内容。
<candidates>
{chr(10).join(candidates)}
</candidates>
<covered>
judgment: {json.dumps(distilled.get('judgment', []), ensure_ascii=False)}
actions: {json.dumps(distilled.get('actions', []), ensure_ascii=False)}
</covered>""",
        SCHEMA_MISSING,
    )
    return {"year": year, "candidates": len(candidates), "missing": out["missing"]}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S", stream=sys.stderr)
    parser = argparse.ArgumentParser(description="decisions 覆盖检查")
    parser.add_argument("--stock", default="601633")
    parser.add_argument("--market", default="cn", choices=["cn", "hk"])
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1] / ".cagent"))
    args = parser.parse_args()
    root = Path(args.root)

    tocs = load_tocs(root, args.market, args.stock)
    knowledge_dir = root / args.market / args.stock / "knowledge"
    report = []
    for year, toc in sorted(tocs.items()):
        path = knowledge_dir / f"fy{year}" / "decisions.json"
        if not path.exists():
            logger.warning(f"fy{year}/decisions.json 不存在，跳过")
            continue
        logger.info(f"检查 {year} …")
        distilled = json.loads(path.read_text(encoding="utf-8"))
        result = check_year(year, toc, distilled)
        report.append(result)
        logger.info(f"  候选 {result['candidates']} 条，遗漏 {len(result['missing'])} 条")

    out_dir = Path("output/wiki") / args.stock / "checks"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "decisions_coverage.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    logger.info(f"报告: {out}")
    for r in report:
        for m in r["missing"]:
            print(f"fy{r['year']} [{m['kind']}] {m['item']}")


if __name__ == "__main__":
    main()
