"""decisions 模块：公司历年经营决策 writer

逐年两段蒸馏：先提取管理层对行业大环境的判断，再提取当年的重大经营决策，
代码拼接为一张三列 markdown 表格，产物
output/wiki/<market>/<stock>/decisions.md。只产内容，不管 UI；页面渲染见 wiki_render.py。

用法：
    uv run cagent/wiki_decisions.py --stock 601633 --market cn
    uv run cagent/wiki_decisions.py --stock 601633 --market cn --force
    uv run cagent/wiki_decisions.py --stock 601633 --market cn --render
"""

import argparse
import logging
import re
import sys
import textwrap
from pathlib import Path

from cagent.agent import Agent
from cagent.chunk import Toc, report_year
from cagent.wiki_render import render_doc

logger = logging.getLogger(__name__)


_judge_examples = [
"""
- 算力成为数字时代关键基础设施，“算力即国力”成为共识，国家战略与产业需求深度绑定。
- 美国出口管制与国内自主可控政策叠加，国产替代加速，但国产 GPU 整体仍处发展初期。
- 大模型应用快速扩张，“智能经济”开启全新产业周期。
- 算力需求从重预训练转向全流程平衡，后训练和推理成为新增长动能。
- 多卡互连能力成为算力竞争核心，高速互连技术直接影响集群有效算力。
- 软件生态成为 AI 芯片易用性关键底座，决定落地场景丰富度。
- 云边端融合，GPU 与 ASIC 各有侧重、长期共存。
- 全球呈英伟达、AMD“一超一强”寡头格局，国内竞争格局尚未明朗。    
""",
"""
- AI 行业加速发展，模型能力、智能体应用与变现模式成熟共同扩展产业上限。
- 编程领域将迈向 L4–L5 级智能，从"工具"走向"同事级"协作。
- 办公领域将复刻编程领域上一年的进步速度。
- 多模态创作将走向"直出可交付"的中长内容，并出现流式、实时输出的新形态。
- 三件事叠加意味着更大规模的智能供给爆发与应用层创新窗口期，需求将被放大至全新量级。
- AI 平台公司价值可概括为：智能密度 × Token 吞吐能力。
"""
]

_decide_examples = [
"""
- 坚持全栈自主研发路线：核心 GPU IP、指令集、架构全部自主，不依赖第三方技术授权。
- 采用 Fabless 经营模式：专注芯片研发设计与销售，晶圆制造、封装测试、板卡集成全部委外。
- 执行“量产一代、在研一代、规划一代”研发策略：基于 IPD 体系推进产品迭代。
- 推进国产供应链布局：提早规划并设计基于国产供应链的产品，保障供应安全与交付稳定。
- 构建“1+6+X”生态与商业布局：以数字算力底座为核心，覆盖金融、医疗、能源、教科研、交通、大文娱六大行业，布局具身智能、低空经济等前沿方向。
- 登陆科创板：2025 年 12 月 17 日在上交所科创板挂牌上市，健全治理与内控体系。
- 推动软件生态开源：2025 年 2 月开放 MXMACA 开源社区，提供 SDK 公开下载与英文开发文档。
- 布局下一代产品：推进曦云 C600 量产、曦索 X206 与曦云 C700 研发及产业化。
- 实施股权激励：报告期确认较大金额股份支付费用，绑定核心团队。
""",
"""
- 向 AI 平台型公司转型：以“智能密度 × Token 吞吐能力”为核心价值框架。
- 全模态模型自主研发并持续迭代，不依赖外包或第三方技术授权。
- 开源 M2 系列模型，换取开发者生态。
- C 端订阅制、B 端按量计价：C 端依赖自然增长与口碑传播，B 端签订框架协议并提供专属推理资源池。
- 轻资产运营、租用云算力：以灵活合同获取 GPU 算力，不持有硬件资产，部分协议约定最低采购量。
- 以低成本扩大 Agent 使用规模：降低复杂 Agent 运行成本。
- 内部 AI 原生组织演进：Agent 覆盖近 90% 员工，并将能力产品化为 MiniMax Agent。
- 全球化布局：产品覆盖超 200 个国家和地区，国际收入占比约 73%。
- 研发开支全部费用化，不资本化内部开发成本。
- 实施股权激励绑定核心人才。
- 登陆港交所：2026 年 1 月 9 日以第 18C 章上市，募资净额约 52.93 亿港元，90% 投向研发。
"""
]


def _judge_prompt() -> str:
    return textwrap.dedent(f"""
        <requirement>
        你在阅读一份上市公司年报，提取管理层对行业环境的判断

        - 删掉任何管理层吹嘘内容，要求语言极度简洁凝炼
        - 全文控制在500字内，语言用简体中文
        </requirement>

        <examples>
        {"\n".join(f"<example>{e}</example>" for e in _judge_examples)}
        </examples>""").strip()


def _decide_prompt() -> str:
    return textwrap.dedent(f"""
        <requirement>
        你在阅读一份上市公司年报，提取管理层的重大经营决策

        - 删掉任何管理层吹嘘内容，要求语言极度简洁凝炼
        - 全文控制在500字内，语言用简体中文
        </requirement>

        <examples>
        {"\n".join(f"<example>{e}</example>" for e in _decide_examples)}
        </examples>""").strip()

def _cell(text: str) -> str:
    lines = text.strip().replace("|", r"\|").split("\n")
    out, i = [], 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith(("- ", "* ")):  # 无序列表
            items = []
            while i < len(lines) and lines[i].strip().startswith(("- ", "* ")):
                items.append(f"<li>{lines[i].strip()[2:]}</li>")
                i += 1
            out.append("<ul>" + "".join(items) + "</ul>")
        elif re.match(r"^\d+\. ", line):  # 有序列表
            items = []
            while i < len(lines) and re.match(r"^\d+\. ", lines[i].strip()):
                items.append(f"<li>{re.sub(r'^\d+\. ', '', lines[i].strip())}</li>")
                i += 1
            out.append("<ol>" + "".join(items) + "</ol>")
        else:
            out.append(line)
            i += 1
    # 段落之间用空行分隔；HTML 块内部不再加 <br>
    html = "\n".join(out)
    return re.sub(r"\n{2,}", "<br><br>", re.sub(r"(?<!\")\n(?!\<)", "<br>", html))


def gen_decisions(agent: Agent, stock: str, market: str, root: Path) -> str:
    derived = root / market / stock / "derived"
    mds = sorted(derived.glob("*.md"))
    if not mds:
        raise SystemExit(f"{derived} 没有年报 md，先跑 ocr/cli.py")
    md_by_year = {report_year(agent, p): p for p in mds}

    rows = []
    for year, md_path in sorted(md_by_year.items())[-5:]:
        toc = Toc(agent, md_path)
        logger.info(f"阅读: {md_path.name}")

        pick_task = "找出管理层和董事会分析讨论相关章节"
        picked = toc.pick_chapters(toc.chapters, pick_task)
        logger.info(f"选中章节: {[toc.chapters[i]['title'] for i in picked]}")
        chunks = [chunk for i in picked for chunk in toc.chapter_text_chunk(i)]

        logger.info(f"{year} gen …")
        judge = agent.chat_reduce_chunks(_judge_prompt(), chunks).strip()
        decide = agent.chat_reduce_chunks(_decide_prompt(), chunks).strip()
        if not judge or not decide:
            raise ValueError(f"{year} decisions 分析异常")

        rows.append(f"| {year} | {_cell(judge)} | {_cell(decide)} |")

    header = "| 年份 | 管理层对行业大环境的判断 | 当年的重大经营决策 |\n| --- | --- | --- |"
    return header + "\n" + "\n".join(rows) + "\n"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S", stream=sys.stderr)
    parser = argparse.ArgumentParser(description="decisions 模块：蒸馏历年经营决策")
    parser.add_argument("--stock", default="601633")
    parser.add_argument("--market", default="cn", choices=["cn", "hk"])
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1] / ".cagent"))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--render", action="store_true", help="生成后渲染 decisions.html")
    args = parser.parse_args()

    output_dir = Path(__file__).resolve().parents[1] / "output" / "wiki" / f"{args.market}" / f"{args.stock}"
    output_dir.mkdir(parents=True, exist_ok=True)

    md_output_path = output_dir / "decisions.md"
    if md_output_path.exists() and not args.force:
        doc = md_output_path.read_text(encoding="utf-8")

    else:
        root = Path(args.root)
        agent = Agent(trace=True)
        doc = gen_decisions(agent, args.stock, args.market, root)
        md_output_path.write_text(doc, encoding="utf-8")

    print(doc)

    if args.render:
        html_output_path = output_dir / "decisions.html"
        html_output_path.write_text(render_doc(doc, title=f"{args.stock} · 历年经营决策", ticker=args.stock, layout="decisions"), encoding="utf-8")
        logger.info(f"渲染: {html_output_path}")


if __name__ == "__main__":
    main()
