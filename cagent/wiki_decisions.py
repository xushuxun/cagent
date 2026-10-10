"""decisions 模块：公司历年经营决策 writer

逐年蒸馏管理层对行业大环境的判断与当年的重大经营决策，
产物落位 knowledge/fy<year>/decisions.json，各年文档在 reader 侧拼接为完整栏目。

用法：
    uv run cagent/wiki_decisions.py --stock 601633 --market cn
    uv run cagent/wiki_decisions.py --stock 601633 --market cn --years 2024,2025
"""

import argparse
import json
import logging
import sys
import textwrap
from pathlib import Path

from cagent.agent import Agent
from cagent.chunk import Toc, report_year

logger = logging.getLogger(__name__)


_decision_examples = [
    {
        "pages": """## (三) 所处行业情况
大模型的爆发引燃了GPU市场巨变，智算中心资本投入急剧增长，GPU在计算领域的应用快速超越其在图形渲染领域的应用，带动GPU整体市场规模高速增长。根据Verified Market Research的数据，2024年全球GPU市场规模为773.9亿美元，2030年有望达到4,724.5亿美元。根据弗若斯特沙利文数据，2024年中国AI加速芯片市场规模约为1,425.37亿元，同比增长98.49%，2025年中国AI加速芯片市场规模预计增至2,398.00亿元。在国际地缘政治加剧的背景下，中国加快了智能算力领域的战略布局，国内智算中心的快速建设推动了AI芯片的需求不断抬升。

## 二、 经营情况讨论与分析
2025年，全球人工智能行业在开源大模型快速普及的推动下，正式迈入规模化落地与技术普惠的新阶段。同时，受美国高性能GPU/AI芯片出口管制与国内自主可控市场发展等因素影响，国产人工智能芯片公司迎来黄金发展期。因此长期来看，未来GPU和ASIC将各有侧重、互为补充、长期共存。

报告期内，公司依托持续高强度研发投入形成的全自研GPU芯片及软硬件生态优势，推动产品在智算中心、运营商、金融、能源等重点场景实现规模化落地。公司秉承"量产一代、在研一代、规划一代"的产品研发策略，2025年7月24日，公司于WAIC大会上发布首款基于全国产工艺的曦云C600系列，曦云C600不仅在算力上较上一代产品曦云C500有较大提升，还在精度、HBM上有新技术的应用，为复杂地缘政治背景下的供应链安全及稳定提供了保障。曦云C600于2025年末实现风险量产，并预计于2026年上半年实现量产销售。围绕"1+6+X"生态与商业布局，公司产品相继应用部署于10余个智算集群，算力网络覆盖国家人工智能公共算力平台、运营商智算平台和商业化智算中心。报告期内，公司研发投入102,739.29万元，研发投入占营业收入比例为62.49%，拥有675人的研发团队，占员工总人数的73%。""",
        "output": """<table class="stats">
<tr><th>年份</th><th>管理层对行业大环境的判断</th><th>当年的重大经营决策</th></tr>
<tr><td class="n">2025</td><td class="q">大模型爆发带动GPU市场高速增长，2024年中国AI加速芯片市场规模同比增长98.49%。<br>管理层认为出口管制与自主可控需求使国产AI芯片迎来黄金发展期，GPU与ASIC将长期共存。</td><td>7月24日WAIC大会发布曦云C600系列，实现全国产工艺，年末风险量产<br>围绕“1+6+X”布局生态，产品部署10余个智算集群<br>研发投入10.27亿元占营收62.49%，研发团队675人占员工73%</td></tr>
</table>""",
    },
    {
        "pages": """2025年，我们构建了全模态的研发能力，语言、视频、语音、音乐等各主要模态均拥有了具备全球竞争力的模型。同时，不断通过技术创新给全球用户带来更好的体验，升级我们的AI原生产品，包括面向企业客户的开放平台，和面向消费者的MiniMax Agent、海螺AI、Talkie／星野等。全球化布局也走得更深更实。截至2025年12月31日，MiniMax累计服务超过200个国家及地区的逾2.36亿名用户，以及来自超过100个国家及地区的21.4万企业客户以及开发者。

在语言模型方面，2025年第四季度我们更新了M2、M2.1、M2-her三款模型，M2发布后迅速获得了全球开发者社区的认可，成为OpenRouter上首个日Token消耗量超过500亿的中国模型，并登顶HuggingFace全球热榜第一。2025年10月，我们发布了视频模型Hailuo 2.3，我们发布的语音模型Speech 2.6支持40多种语言。2026年2月，我们发布了M2.5，在编程、工具调用和办公等生产力场景全面达到全球顶尖水平。

我们认为接下来一年的模型智能水平会进一步提升。编程领域将迎来L4至L5级别的智能，从「工具」走向「同事级」协作；办公领域将复刻去年编程领域的进步速度。展望未来，在公司战略层面，我们会从基础模型公司向AI时代的平台型公司迈进。我们也在持续向AI原生组织演进，我们内部的Agent实习生已经覆盖了近90%的员工。2026年1月，我们将沉淀的能力产品化，推出MiniMax Agent AI-native Workspace。""",
        "output": """<table class="stats">
<tr><th>年份</th><th>管理层对行业大环境的判断</th><th>当年的重大经营决策</th></tr>
<tr><td class="n">2025</td><td class="q">开源大模型快速普及，人工智能迈入规模化落地与技术普惠的新阶段。<br>管理层预计编程领域将迎来L4至L5级别智能，应用层面临创新窗口期。</td><td>四季度发布M2、M2.1、M2-her三款语言模型，M2登顶HuggingFace热榜<br>10月发布视频模型Hailuo 2.3与语音模型Speech 2.6<br>向AI原生组织演进，Agent实习生覆盖近90%员工<br>明确从基础模型公司向AI平台型公司转型的战略方向</td></tr>
</table>""",
    },
]



def _decision_prompt() -> str:
    return textwrap.dedent(f"""
        <requirement>
        你在阅读一份上市公司年报的"管理层讨论与分析"，蒸馏管理层对行业大环境的判断和当年的重大经营决策，
        输出一个三列 HTML 表格（年份/管理层对行业大环境的判断/当年的重大经营决策），只输出报告期当年一行，判断与决策单元格内多条用 <br> 分隔。

        - 只记当年实际发生的事，不记经营结果数字，禁止编撰；不输出页码和章节名
        - 全文用简体中文
        </requirement>

        <examples>
        {"\n".join(f"<example>\\n<pages>\\n{x['pages']}\\n</pages>\\n<output>\\n{x['output']}\\n</output>\\n</example>" for x in _decision_examples)}
        </examples>""").strip()


_pick_task = "逐年蒸馏管理层对行业大环境的判断与当年的重大经营决策。这类内容通常在年报的“管理层讨论与分析”章节，请结合目录标题选择要精读的章节（可多个）。"


def _load_tocs(agent: Agent, stock: str, market: str, root: Path) -> dict[int, tuple[Toc, Path]]:
    derived = root / market / stock / "derived"
    mds = sorted(derived.glob("*.md"))
    if not mds:
        raise SystemExit(f"{derived} 没有年报 md，先跑 ocr/cli.py")
    return {report_year(agent, p): (Toc(agent, p), p) for p in mds}


def gen_decisions(agent: Agent, stock: str, market: str, root: Path, force: bool, years: list[int] | None = None) -> list[tuple[int, str]]:
    knowledge_dir = root / market / stock / "knowledge"
    docs = []
    for year, (toc, md_path) in sorted(_load_tocs(agent, stock, market, root).items()):
        if years and year not in years:
            continue
        out_path = knowledge_dir / md_path.stem / "decisions.json"
        if out_path.exists() and not force:
            logger.info(f"跳过（已存在）: {out_path}")
            docs.append((year, json.loads(out_path.read_text(encoding="utf-8"))["doc"]))
            continue

        logger.info(f"{year} …")
        picked = toc.pick_chapters(toc.chapters, _pick_task)
        logger.info(f"  选中章: {[toc.chapters[i]['title'] for i in picked]}")
        chunks = [chunk for i in picked for chunk in toc.chapter_text_chunk(i)]

        doc = agent.chat_reduce_chunks(_decision_prompt(), chunks)
        if not doc.strip():
            raise ValueError(f"{year} decisions 分析异常")

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps({"year": year, "topic": "decisions", "source": toc.gen_source(picked), "doc": doc}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        logger.info(f"输出: {out_path}")
        docs.append((year, doc))
    return docs


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S", stream=sys.stderr)
    parser = argparse.ArgumentParser(description="decisions 模块：蒸馏历年经营决策，产物 knowledge/fy<year>/decisions.json")
    parser.add_argument("--stock", default="601633")
    parser.add_argument("--market", default="cn", choices=["cn", "hk"])
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1] / ".cagent"))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--years", help="只跑指定年份，逗号分隔，如 2024,2025")
    args = parser.parse_args()
    years = [int(y) for y in args.years.split(",")] if args.years else None

    agent = Agent(trace=True)
    gen_decisions(agent, args.stock, args.market, Path(args.root), args.force, years)



if __name__ == "__main__":
    main()
