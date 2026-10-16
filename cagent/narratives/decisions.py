"""历年经营决策：逐年读年报原文，提管理层判断与当年决策（一个 section 一个文件）

    uv run cagent/wiki_decisions.py --stock 601633 --market cn
"""

import argparse
import logging
import sys
from pathlib import Path

from cagent.llm import LLM
from cagent.narratives.engine import Section, write_section

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


DECISIONS = Section(
    id="decisions",
    title="历年经营决策",
    pick_task="找出管理层和董事会分析讨论相关章节",
    parts=(
        ("管理层对行业大环境的判断", "你在阅读一份上市公司年报，提取管理层对行业环境的判断。全文控制在 500 字以内。"),
        ("当年的重大经营决策", "你在阅读一份上市公司年报，提取管理层的重大经营决策。全文控制在 500 字以内。"),
    ),
    examples=tuple(_judge_examples + _decide_examples),
    per_report=True,
)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S", stream=sys.stderr)
    parser = argparse.ArgumentParser(description="历年经营决策")
    parser.add_argument("--stock", default="601633")
    parser.add_argument("--market", default="cn", choices=["cn", "hk"])
    parser.add_argument("--root", type=Path, default=Path(".cagent"))
    parser.add_argument("--out", type=Path, default=Path("output/wiki"))
    parser.add_argument("--force", action="store_true", help="重跑（默认复用已有小节）")
    args = parser.parse_args()

    target = args.out / args.market / args.stock / "decisions.md"
    if target.exists() and not args.force:
        logger.info(f"复用: {target}")
        return
    agent = LLM(trace=True)
    doc = write_section(agent, DECISIONS, args.stock, args.market, args.root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(doc, encoding="utf-8")
    logger.info(f"写出 {target}")
    print(agent.ledger.summary())


if __name__ == "__main__":
    main()
