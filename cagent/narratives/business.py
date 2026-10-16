"""生意模式：读年报原文把生意讲清楚（一个 section 一个文件）

关键投入用财务证据量化（数字受校验），引用原文事实标注页码。

    uv run cagent/wiki_business.py --stock 601633 --market cn
"""

import argparse
import logging
import sys
from pathlib import Path

from cagent.llm import LLM
from cagent.narratives.engine import Section, write_section
from cagent.series import evidence, load_series

logger = logging.getLogger(__name__)

_intro_examples = [
    """
#### 定位与产品

公司主营高性能 GPU 芯片及计算平台自主研发，聚焦训练与推理、通用计算、图形渲染三大领域，配套自研软件栈。产品覆盖曦思 N（智算推理）、曦云 C（训推一体）、曦彩 G（图形渲染）系列，形态由板卡延伸至服务器、一体机及智算集群。采用 Fabless 模式，专注芯片研发设计与销售，晶圆制造、封装测试全部委外。

#### 经营模式

**研发**：基于 IPD 理念构建覆盖概念至量产的研发体系，执行「量产一代、在研一代、规划一代」策略，研发人员占比约七成。**采购**：向晶圆厂采购定制晶圆，向封测厂采购封测服务，向加工厂商采购板卡加工服务。受出口管制影响，上游要求大额预付款，公司以预付锁货加战略备货保障供应，并培育国产供应链。**生产**：晶圆加工、封装测试、板卡集成全部委外；公司负责规格定义、架构设计、核心 IP 开发与验证并交付设计版图。**销售**：直销与经销结合，直销参与公开招标或商务谈判，经销由经销商向下游销售并给予价格让利；下游享有信用期，部分客户背靠背结算，亦存在客户预付货款。**交付**：按需求分档交付板卡、整机或智算集群。

#### 客户

主要客户为服务器厂商、系统集成商和智算中心建设方，终端应用于教科研、金融、交通、能源等行业及国家人工智能公共算力平台。前五大客户收入占比高，客户集中度较高，不存在对单一客户的严重依赖。收入几乎全部来自中国内地。

#### 资本

资金主要来源于股权融资，另有客户预收款与回款。资金投向研发人员薪酬、EDA 软件、IP 授权、光罩，以及向上游预付的晶圆、HBM 采购款和战略备货。公司回款慢于付款，经营积累有限，资本投入大、回报周期长。尚未盈利，未现金分红。募集资金投向后续几代高性能 GPU 研发及产业化项目，均围绕主营业务。
""",


    """
#### 定位与产品

MiniMax 是一家全球化人工智能基础模型公司，主营自研全模态大模型（文本、视频、语音、音乐、图像），并以此运营 AI 原生应用及面向企业与开发者的开放平台。

#### 经营模式

**研发**：全模态模型均自主开发并持续迭代。研发支出主要为训练用云算力费用与研发人员薪酬，不依赖外包或第三方技术授权。**采购**：向云基础设施供应商租用 GPU 算力，轻资产运营、不持有硬件资产；部分协议约定最低采购量。另采购数据标注、内容审核与营销推广服务。**产品化**：将模型能力封装为 AI 原生应用（海螺 AI、Talkie/星野等）及开放平台 API，云端交付、无实体物流。**销售**：C 端产品经自有网页端及应用商店分发，订阅制定价，获客主要依靠自然增长与口碑传播；B 端采取直销与渠道伙伴结合，API 按调用量计价，企业客户签订框架协议并获得专属推理资源池。**交付**：交付方式为在线调用。

#### 客户

客户包括全球个人用户、企业与开发者及在应用内投放广告的第三方广告平台。客户结构分散，前五大客户占比持续下降，不存在对单一客户的严重依赖。收入约七成来自中国内地以外地区。

#### 资本

资金主要来源于优先股融资和 IPO 募资，另有个人用户订阅与充值（预付）、广告平台投放费、企业客户按量月结及许可费等经营回款。最大支出为向云厂商支付的训练与推理算力费用，其次为研发人员薪酬与营销开支。公司轻资产运营、不持有硬件，再投入以算力和研发人员为主，不以固定资产形式沉淀。研发开支远大于收入，尚未盈利，未现金分红，优先股于上市时转为普通股。
""",
]


BUSINESS = Section(
    id="business",
    title="生意模式",
    pick_task="找出公司介绍和管理层讨论分析章节",
    parts=(
        (
            "生意模式",
            (
                "你在阅读一份上市公司年报，要把商业模式讲清楚，只关注核心生意过程机制，去掉报告期短期信息。"
                "说清：钱从哪来（客户与收入结构）、钱花在哪（成本与投入结构）、靠什么赚（价值链上的位置与壁垒）。"
                "关键投入（研发、产能、渠道）可以用财务证据里的数字量化。全文控制在 400 字以内。"
            ),
        ),
    ),
    examples=tuple(_intro_examples),
    with_evidence=True,
)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S", stream=sys.stderr)
    parser = argparse.ArgumentParser(description="生意模式")
    parser.add_argument("--stock", default="601633")
    parser.add_argument("--market", default="cn", choices=["cn", "hk"])
    parser.add_argument("--root", type=Path, default=Path(".cagent"))
    parser.add_argument("--out", type=Path, default=Path("output/wiki"))
    parser.add_argument("--force", action="store_true", help="重跑（默认复用已有小节）")
    args = parser.parse_args()

    target = args.out / args.market / args.stock / "business.md"
    if target.exists() and not args.force:
        logger.info(f"复用: {target}")
        return
    agent = LLM(trace=True)
    evidence_text = evidence(load_series(args.root, args.market, args.stock))
    doc = write_section(agent, BUSINESS, args.stock, args.market, args.root, evidence_text)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(doc, encoding="utf-8")
    logger.info(f"写出 {target}")
    print(agent.ledger.summary())


if __name__ == "__main__":
    main()
