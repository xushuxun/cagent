"""decisions 模块：公司历年经营决策 writer

逐年蒸馏管理层对行业大环境的判断（judgment）与当年的重大经营决策（actions），
附章节页码定位索引，产物落位 knowledge/fy<year>/decisions.json。
"""

import json
import logging
import textwrap
from pathlib import Path

from cagent.agent import Agent, chunk_text
from cagent.chunk import Toc, load_tocs

logger = logging.getLogger(__name__)

fewshots = [
    {
        "pages": """2021年，尽管面对复杂严峻的发展环境，中国疫情防控及经济社会发展仍保持全球领先地位。根据中国汽车工业协会数据显示，2021年汽车产销分别完成2,608.2万辆和2,627.5万辆，同比分别增长3.4%和3.8%。从汽车行业发展趋势来看，汽车市场需求将保持稳定，随着芯片供应不足、原材料价格高位运行等问题在2022年逐步改善，预计今年汽车市场将继续呈现稳中向好的发展态势。

2021年，本集团实现营业总收入1,364.05亿元，同比增长32.04%；净利润67.81亿元，同比增长26.45%。本集团坚定不移地推进新能源与智能化发展，发布了2025战略，明确了"绿智潮玩"的发展战略。哈弗、魏牌、欧拉、坦克及长城皮卡五大品牌协同发展，沙龙品牌正式孵化。本集团海外市场持续突破，泰国罗勇工厂正式投产，巴西工厂项目启动。""",
        "output": {
            "judgment": ["2021年汽车产销恢复增长，芯片短缺与原材料高位运行贯穿全年。", "管理层预计2022年汽车市场稳中向好。"],
            "actions": ["发布2025战略，明确“绿智潮玩”发展路线", "沙龙品牌正式孵化，五大品牌协同", "泰国罗勇工厂投产，巴西工厂项目启动"],
        },
    }
]

REQUIREMENTS = """读 <pages>（某财年年报"管理层讨论与分析"的一部分），把管理层对行业大环境的判断和当年的重大经营决策合并进 <current>，返回合并后的完整版本。
judgment 是 1–2 句话的列表，管理层视角，以报告期当年的回顾为锚，可并入对来年的展望（写明"预计"），不得只写展望。
actions 是 3–10 句话的列表，每句不超过30字，记当年实际发生的事；每句必须有具体事实（谁、做了什么、对象或量级），禁止"深化""拥抱""推进"这类无新信息的表述；持续推进的长期战略只在当年有新进展时记录。
actions 不记经营结果数字（销量、营收、利润及同比），经营结果归指标层。
时间归属必须属于本报告期：早年事项只有在本年收官、完成或终止时才记，并写明是本年的进展，不得写成当年发布。
范围是战略、品牌矩阵调整、产能基地、投资并购、资本动作、组织变革；单一车型与零部件细节归产品模块，不在此记录。
每个事实必须能在 <pages> 或 <current> 中找到依据，禁止编撰；没有新内容时 updated=false，judgment 和 actions 原样返回。"""

SCHEMA = {
    "type": "object",
    "properties": {
        "updated": {"type": "boolean"},
        "judgment": {"type": "array", "items": {"type": "string"}},
        "actions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["updated", "judgment", "actions"],
}

PICK_TASK = "逐年蒸馏管理层对行业大环境的判断与当年的重大经营决策。这类内容通常在年报的“管理层讨论与分析”章节，请结合目录标题选择要精读的章节（可多个）。"


def gen_year_row(agent: Agent, toc: Toc, chapters: list[dict]) -> dict:
    current = {"judgment": [], "actions": []}
    example = textwrap.dedent(f"""
        <example>
        <pages>
        {fewshots[0]["pages"]}
        </pages>
        <output>
        {json.dumps(fewshots[0]["output"], ensure_ascii=False)}
        </output>
        </example>
    """).strip()
    for chapter in chapters:
        i = toc.chapters.index(chapter)
        for chunk in chunk_text(toc.chapter_text(i)):
            prompt = textwrap.dedent(f"""
                {REQUIREMENTS}
                {example}

                <current>
                {json.dumps(current, ensure_ascii=False)}
                </current>

                <pages>
                {chunk}
                </pages>
            """).strip()
            out = agent.chat_json(prompt, SCHEMA)
            if out.pop("updated", True):
                current = out
    if not current["judgment"] and not current["actions"]:
        raise ValueError(f"{[c['title'] for c in chapters]} 提取结果为空")
    return current


def gen_decisions(agent: Agent, stock: str, market: str, root: Path, force: bool, years: list[int] | None = None) -> None:
    knowledge_dir = root / market / stock / "knowledge"
    for year, toc in sorted(load_tocs(root, market, stock).items()):
        if years and year not in years:
            continue
        out_path = knowledge_dir / f"fy{year}" / "decisions.json"
        if out_path.exists() and not force:
            logger.info(f"跳过（已存在）: {out_path}")
            continue
        logger.info(f"{year} …")
        picked = agent.pick_chapters(toc.chapters, PICK_TASK)
        chapters = [toc.chapters[i] for i in picked]
        logger.info(f"  选中章: {[c['title'] for c in chapters]}")
        row = gen_year_row(agent, toc, chapters)
        source = {
            "chapters": [
                {
                    "chapter": c["title"],
                    "md_page": [c["md_page"], toc.chapters[i + 1]["md_page"] - 1],
                }
                for i, c in zip(picked, chapters)
            ]
        }
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps({"fy": year, "topic": "decisions", "source": source, **row}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        logger.info(f"输出: {out_path}")
