"""decisions 模块：公司历年经营决策 writer

逐年蒸馏管理层对行业大环境的判断与当年的重大经营决策，
产物落位 knowledge/fy<year>/decisions.json，各年文档在 reader 侧拼接为完整栏目。
"""

import json
import logging
import textwrap
from pathlib import Path

from cagent.agent import Agent, chunk_text
from cagent.chunk import Toc, load_tocs, script
from cagent.kdoc import SCHEMA

logger = logging.getLogger(__name__)

fewshots = [
    {
        "pages": """## (三) 所处行业情况
大模型的爆发引燃了GPU市场巨变，智算中心资本投入急剧增长，GPU在计算领域的应用快速超越其在图形渲染领域的应用，带动GPU整体市场规模高速增长。根据Verified Market Research的数据，2024年全球GPU市场规模为773.9亿美元，2030年有望达到4,724.5亿美元。根据弗若斯特沙利文数据，2024年中国AI加速芯片市场规模约为1,425.37亿元，同比增长98.49%，2025年中国AI加速芯片市场规模预计增至2,398.00亿元。在国际地缘政治加剧的背景下，中国加快了智能算力领域的战略布局，国内智算中心的快速建设推动了AI芯片的需求不断抬升。

## 二、 经营情况讨论与分析
2025年，全球人工智能行业在开源大模型快速普及的推动下，正式迈入规模化落地与技术普惠的新阶段。同时，受美国高性能GPU/AI芯片出口管制与国内自主可控市场发展等因素影响，国产人工智能芯片公司迎来黄金发展期。因此长期来看，未来GPU和ASIC将各有侧重、互为补充、长期共存。

报告期内，公司依托持续高强度研发投入形成的全自研GPU芯片及软硬件生态优势，推动产品在智算中心、运营商、金融、能源等重点场景实现规模化落地。公司秉承"量产一代、在研一代、规划一代"的产品研发策略，2025年7月24日，公司于WAIC大会上发布首款基于全国产工艺的曦云C600系列，曦云C600不仅在算力上较上一代产品曦云C500有较大提升，还在精度、HBM上有新技术的应用，为复杂地缘政治背景下的供应链安全及稳定提供了保障。曦云C600于2025年末实现风险量产，并预计于2026年上半年实现量产销售。围绕"1+6+X"生态与商业布局，公司产品相继应用部署于10余个智算集群，算力网络覆盖国家人工智能公共算力平台、运营商智算平台和商业化智算中心。报告期内，公司研发投入102,739.29万元，研发投入占营业收入比例为62.49%，拥有675人的研发团队，占员工总人数的73%。""",
        "output": {
            "updated": True,
            "doc": """<table>
<tr><th>年份</th><th>管理层对行业大环境的判断</th><th>当年的重大经营决策</th></tr>
<tr><td>2025</td><td>大模型爆发带动GPU市场高速增长，2024年中国AI加速芯片市场规模同比增长98.49%。<br>管理层认为出口管制与自主可控需求使国产AI芯片迎来黄金发展期，GPU与ASIC将长期共存。</td><td>7月24日WAIC大会发布曦云C600系列，实现全国产工艺，年末风险量产<br>围绕“1+6+X”布局生态，产品部署10余个智算集群<br>研发投入10.27亿元占营收62.49%，研发团队675人占员工73%</td></tr>
</table>""",
        },
    },
    {
        "pages": """2025年，我們構建了全模態的研發能力，語言、視頻、語音、音樂等各主要模態均擁有了具備全球競爭力的模型。同時，不斷通過技術創新給全球用戶帶來更好的體驗，升級我們的AI原生產品，包括面向企業客戶的開放平台，和面向消費者的MiniMax Agent、海螺AI、Talkie／星野等。全球化佈局也走得更深更實。截至2025年12月31日，MiniMax累計服務超過200個國家及地區的逾2.36億名用戶，以及來自超過100個國家及地區的21.4萬企業客戶以及開發者。

在語言模型方面，2025年第四季度我們更新了M2、M2.1、M2-her三款模型，M2發佈後迅速獲得了全球開發者社區的認可，成為OpenRouter上首個日Token消耗量超過500億的中國模型，並登頂HuggingFace全球熱榜第一。2025年10月，我們發佈了視頻模型Hailuo 2.3，我們發佈的語音模型Speech 2.6支持40多種語言。2026年2月，我們發佈了M2.5，在編程、工具調用和辦公等生產力場景全面達到全球頂尖水平。

我們認為接下來一年的模型智能水平會進一步提升。編程領域將迎來L4至L5級別的智能，從「工具」走向「同事級」協作；辦公領域將複刻去年編程領域的進步速度。展望未來，在公司戰略層面，我們會從基礎模型公司向AI時代的平台型公司邁進。我們也在持續向AI原生組織演進，我們內部的Agent實習生已經覆蓋了近90%的員工。2026年1月，我們將沉澱的能力產品化，推出MiniMax Agent AI-native Workspace。""",
        "output": {
            "updated": True,
            "doc": """<table>
<tr><th>年份</th><th>管理層對行業大環境的判斷</th><th>當年的重大經營決策</th></tr>
<tr><td>2025</td><td>開源大模型快速普及，人工智能邁入規模化落地與技術普惠的新階段。<br>管理層預計編程領域將迎來L4至L5級別智能，應用層面臨創新窗口期。</td><td>四季度發佈M2、M2.1、M2-her三款語言模型，M2登頂HuggingFace熱榜<br>10月發佈視頻模型Hailuo 2.3與語音模型Speech 2.6<br>向AI原生組織演進，Agent實習生覆蓋近90%員工<br>明確從基礎模型公司向AI平台型公司轉型的戰略方向</td></tr>
</table>""",
        },
    },
]

REQUIREMENTS = """读 <pages>（某财年年报"管理层讨论与分析"的一部分），把管理层对行业大环境的判断和当年的重大经营决策合并进 <current> 的文档，返回合并后的完整版本。
判断是 1–2 句话，管理层视角，以报告期当年的回顾为锚，可并入对来年的展望（写明"预计"），不得只写展望。
决策是 3–10 句话，每句不超过30字，记当年实际发生的事；每句必须有具体事实（谁、做了什么、对象或量级），禁止"深化""拥抱""推进"这类无新信息的表述；持续推进的长期战略只在当年有新进展时记录。
决策不记经营结果数字（销量、营收、利润及同比），经营结果归指标层。
时间归属必须属于本报告期：早年事项只有在本年收官、完成或终止时才记，并写明是本年的进展，不得写成当年发布。
范围是战略、品牌矩阵调整、产能基地、投资并购、资本动作、组织变革；单一车型与零部件细节归产品模块，不在此记录。
每个事实必须能在 <pages> 或 <current> 中找到依据，禁止编撰；没有新内容时 updated=false，doc 原样返回。
输出文档的语言（简繁体、用词）与 <pages> 保持一致。

输出一个 HTML 表格（<table>）：首行 <th> 表头三列（年份/管理层对行业大环境的判断/当年的重大经营决策，列名随原文语种），本报告期一行；判断与决策单元格内多条用 <br> 分隔。"""

PICK_TASK = "逐年蒸馏管理层对行业大环境的判断与当年的重大经营决策。这类内容通常在年报的“管理层讨论与分析”章节，请结合目录标题选择要精读的章节（可多个）。"


def gen_year_row(agent: Agent, year: int, toc: Toc, chapters: list[dict]) -> dict:
    current = {"doc": ""}
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
    for chapter in chapters:
        i = toc.chapters.index(chapter)
        for chunk in chunk_text(toc.chapter_text(i)):
            prompt = textwrap.dedent(f"""
                {REQUIREMENTS}
                {example}

                本报告期是 {year} 年。原文是 {script(toc.text)}，doc 必须与原文同语种。

                <current>
                {json.dumps(current, ensure_ascii=False)}
                </current>

                <pages>
                {chunk}
                </pages>
            """).strip()
            out = agent.chat_json(prompt, SCHEMA)
            if out.pop("updated", True) and out.get("doc", "").strip():
                current = out
    if not current["doc"].strip():
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
        row = gen_year_row(agent, year, toc, chapters)
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
            json.dumps({"fy": year, "topic": "decisions", "source": source, "doc": row["doc"]}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        logger.info(f"输出: {out_path}")
