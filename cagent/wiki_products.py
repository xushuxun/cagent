"""products 模块：主要产品 writer

通读指定年份年报（默认最新），整理主要车型类别/品牌的销量与定位。
产物落位 knowledge/fy<year>/products.json。
"""

import json
import logging
import textwrap
from pathlib import Path

from cagent.agent import Agent, chunk_text
from cagent.chunk import load_tocs

logger = logging.getLogger(__name__)

fewshots = [
    {
        "pages": """2025年，我們升級了我們的AI原生產品，包括面向企業客戶的開放平台，和面向消費者的MiniMax Agent、海螺AI、Talkie／星野等。2025年全年MiniMax總收入同比增長158.9%達到7,900萬美元，其中超過70%的收入來自國際市場。截至2025年12月31日，MiniMax累計服務超過200個國家及地區的逾2.36億名用戶，以及來自超過100個國家及地區的21.4萬企業客戶以及開發者。在語言模型方面，2025年第四季度我們更新了M2、M2.1、M2-her三款模型，M2發佈後迅速獲得了全球開發者社區的認可，成為OpenRouter上首個日Token消耗量超過500億的中國模型。2026年2月，我們發佈了M2.5，在編程、工具調用和辦公等生產力場景全面達到全球頂尖水平。在多模態方面，2025年10月，我們發佈了視頻模型Hailuo 2.3，同時推出更快速的Fast模型；我們發佈的語音模型Speech 2.6支持40多種語言，音樂模型Music 2.0與2.5單首作品時長可達5分鐘。

客戶合同所得收入細分：AI原生產品53,075千美元，開放平台及其他基於AI的企業服務25,963千美元，提供服務所得收入合計79,038千美元。""",
        "output": {
            "updated": True,
            "lines": [
                {"name": "AI原生產品", "scale": "5,307.5 萬美元", "yoy": "—", "note": "海螺AI、Talkie／星野、MiniMax Agent，超七成收入來自國際市場"},
                {"name": "開放平台及企業服務", "scale": "2,596.3 萬美元", "yoy": "—", "note": "API按調用量計價，服務21.4萬企業客戶及開發者"},
                {"name": "語言模型M2系列", "scale": "未單列", "yoy": "—", "note": "年內發佈M2、M2.1、M2-her，2026年2月發佈M2.5"},
                {"name": "視頻、語音、音樂模型", "scale": "未單列", "yoy": "—", "note": "Hailuo 2.3、Speech 2.6、Music 2.0/2.5，視頻累計生成超6億個"},
            ],
        },
    },
    {
        "pages": """公司的主要产品全面覆盖人工智能计算、通用计算（包括科学计算）和图形渲染三大领域，公司先后推出了用于智算推理的曦思N系列GPU、用于训推一体和通用计算的曦云C系列GPU，以及正在研发用于图形渲染的曦彩G系列GPU。

主营业务分产品情况：GPU产品及配件营业收入1,630,746,470.51元，营业收入比上年增减132.74%；IP、技术服务及其他13,338,995.35元，比上年增减59.59%。

产销量情况分析表：主要产品训推一体GPU板卡销售量33,649片，销售量比上年增减147.31%；智算推理GPU板卡销售量4,946片，比上年增减866.02%。销量的变动主要系随着公司产品及服务获得下游客户的广泛认可与持续采购，公司GPU产品出货量显著增长。""",
        "output": {
            "updated": True,
            "lines": [
                {"name": "训推一体GPU板卡", "scale": "3.36 万片", "yoy": "+147.31%", "note": "曦云C系列，2025年7月发布全国产工艺C600"},
                {"name": "智算推理GPU板卡", "scale": "0.49 万片", "yoy": "+866.02%", "note": "曦思N系列，覆盖生成式AI推理场景"},
                {"name": "GPU产品及配件收入", "scale": "16.31 亿元", "yoy": "+132.74%", "note": "占营业收入99.2%"},
                {"name": "图形渲染GPU", "scale": "未单列", "yoy": "—", "note": "曦彩G系列，在研"},
            ],
        },
    }
]

REQUIREMENTS = """你在通读一份上市公司年报，要整理出这家公司主要产品的结构
把 <pages> 里的信息合并进 <current>，返回合并后的完整版本。
lines 每行一个车型类别/品牌/产品线：name 是名称；scale 是报告期销量，照抄报告披露并换算为"万辆"等简洁形式，报告未单独披露的填"未单列"；yoy 是同比，照抄，没有则填"—"；note 一句话定位或要点，不评价。
优先收录报告披露了销量的类别；品牌定位以散文描述补充。
每个数字必须来自 <pages>，禁止编撰；没有新内容时 updated=false，lines 原样返回。"""

SCHEMA = {
    "type": "object",
    "properties": {
        "updated": {"type": "boolean"},
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "scale": {"type": "string"},
                    "yoy": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["name", "scale", "yoy", "note"],
            },
        },
    },
    "required": ["updated", "lines"],
}

PICK_TASK = "你在通读一份上市公司年报的目录。了解主要产品与品牌结构：分车型或分品牌销量、品牌定位，需要阅读哪些章节？"


def gen_products(agent: Agent, stock: str, market: str, root: Path, force: bool, years: list[int] | None = None) -> None:
    tocs = load_tocs(root, market, stock)
    year = max(years) if years else max(tocs)
    toc = tocs[year]
    out_path = root / market / stock / "knowledge" / f"fy{year}" / "products.json"
    if out_path.exists() and not force:
        logger.info(f"跳过（已存在）: {out_path}")
        return

    logger.info(f"{year} …")
    picked = agent.pick_chapters(toc.chapters, PICK_TASK)
    logger.info(f"  选中章: {[toc.chapters[i]['title'] for i in picked]}")
    example = "\n\n".join(
        f"""<example>
<pages>
{x["pages"]}
</pages>
<output>
{json.dumps(x["output"], ensure_ascii=False)}
</output>
</example>"""
        for x in fewshots
    )
    state = {"lines": []}
    for i in picked:
        for chunk in chunk_text(toc.chapter_text(i)):
            prompt = textwrap.dedent(f"""
                {REQUIREMENTS}
                {example}

                <current>
                {json.dumps(state, ensure_ascii=False)}
                </current>

                <pages>
                {chunk}
                </pages>
            """).strip()
            out = agent.chat_json(prompt, SCHEMA)
            if out.pop("updated", True):
                state = out
    if not state["lines"]:
        raise ValueError(f"fy{year} 主要产品为空")

    source = {
        "chapters": [
            {
                "chapter": toc.chapters[i]["title"],
                "md_page": [toc.chapters[i]["md_page"], toc.chapters[i + 1]["md_page"] - 1],
            }
            for i in picked
        ]
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps({"fy": year, "topic": "products", "source": source, "lines": state["lines"]}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    logger.info(f"输出: {out_path}")
