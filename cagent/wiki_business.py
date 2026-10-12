"""business 模块：生意过程 writer

通读指定年份年报（默认最新），蒸馏这门生意如何运转，产物为 Markdown 文档
knowledge/fy<year>/business.json。只产内容，不管 UI；页面渲染见 wiki_render.py。

用法：
    uv run cagent/wiki_business.py --stock 601633 --market cn
    uv run cagent/wiki_business.py --stock 601633 --market cn --force
    uv run cagent/wiki_business.py --stock 601633 --market cn --render
"""

import argparse
import logging
import sys
import textwrap
from pathlib import Path

from cagent.agent import Agent
from cagent.chunk import Toc
from cagent.wiki_render import render_doc

logger = logging.getLogger(__name__)


_intro_examples = [
    """
#### 定位与产品

公司致力于自主研发高性能 GPU 芯片及计算平台，主营业务为研发、设计和销售训练与推理、通用计算、图形渲染三大领域的 GPU 产品，配套提供自研软件栈；产品覆盖曦思 N（智算推理）、曦云 C（训推一体）和曦彩 G（图形渲染）系列，形态从板卡延伸至服务器、一体机及智算集群。公司自成立以来采用 **Fabless 经营模式**，即专注芯片研发设计和销售，晶圆制造、封装测试全部委托专业厂商完成。

#### 经营模式

**研发**：公司构建基于 IPD 理念、覆盖从概念到量产全过程的研发体系，执行「量产一代、在研一代、规划一代」策略，研发人员约占七成。**采购**：向晶圆制造厂采购定制晶圆，向封测厂采购封测服务，向加工厂商采购板卡加工服务；受出口管制影响，上游要求大额预付款，公司以**预付锁货**加**战略备货**保障供应，并培育国产供应链。**生产**：**晶圆加工、封装测试和板卡集成全部委外**，公司负责规格定义、架构设计、核心 IP 开发与验证并交付设计版图，辅以工艺管理和测试支持。**销售**：报告期内，公司采用**直销与经销相结合**的模式——直销参与客户公开招标或商务谈判，签约后按订单发货并提供售后技术支持；经销由经销商向下游销售，公司提供技术培训并给予价格让利；公司给予下游信用期，部分客户"**背靠背**"结算，亦存在客户预付较高比例货款的情形。**交付与售后**：按客户需求从板卡、整机到整簇智算集群分档交付，并提供技术支持与经销商培训。

#### 客户

主要下游客户为服务器厂商、系统集成商和智算中心建设方，终端应用于教科研、金融、交通、能源等行业及国家人工智能公共算力平台；前五大客户收入占比报告期均在七成及以上，**客户集中度相对较高**，2025 年降至六成左右，不存在对单一客户的严重依赖；收入几乎全部来自中国内地。

#### 资本

报告期资金主要来源于**股权融资**——上市前多轮增资及 IPO 募资为最主要来源，及客户预收款与回款；资金主要投向研发人员薪酬、EDA 软件、IP 授权、光罩，以及向上游预付的晶圆、HBM 采购款和战略备货。公司回款慢于付款，经营积累有限，资本投入大、回报周期长；报告期内尚未盈利，股东回报尚未体现，未现金分红。本次募集资金投向后续几代高性能 GPU 研发及产业化项目，均围绕主营业务。
""",


    """
#### 定位与产品

MiniMax 是一家全球化的人工智能基础模型公司，主营业务为自研全模态大模型（文本、视频、语音、音乐、图像），并以此为基础运营 AI 原生应用和面向企业与开发者的开放平台。

#### 经营模式

**研发**：公司高度重视基础模型研究，全模态模型均自主开发并持续迭代，研发支出主要为训练所用的云算力费用和研发人员薪酬，不依赖外包或第三方技术授权。**采购**：公司通过灵活合同向云基础设施供应商**租用 GPU 算力**，**轻资产运营、不持有硬件资产**，部分协议约定最低采购量，付款信用期为 30-90 天；另采购数据标注、内容审核和营销推广服务。**产品化**：公司将模型能力封装为 AI 原生应用（海螺 AI、Talkie/星野等）和开放平台 API，**云端交付、无实体物流**。**销售**：C 端产品经自有网页端及应用商店分发，采取**订阅制**定价，获客主要依靠自然增长与口碑传播；B 端采取直销与渠道伙伴相结合的方式，API **按调用量计价**，企业客户签订框架协议并获提供专属推理资源池。**交付与售后**：交付方式为在线调用，售后服务包括云服务运维、内容审核及多法域合规更新。

#### 客户

公司客户包括全球个人用户（累计超 2 亿）、企业与开发者（付费客户约 2,500 家）及在应用内投放广告的第三方广告平台；客户结构分散，前五大客户占比持续下降，不存在对单一客户的严重依赖；收入约七成来自中国内地以外地区。

#### 资本

报告期，公司资金主要来源于**优先股融资和 IPO 募资**，另有个人用户订阅与充值（**预付**）、广告平台投放费、企业客户按量月结及许可费等经营回款；资金最大支出为向云厂商支付的训练与推理算力费用，其次为研发人员薪酬与营销开支。公司**轻资产运营、不持有硬件**，再投入以算力和研发人员为主，不以固定资产形式沉淀；报告期内研发开支远大于收入，公司尚未盈利，股东回报尚未体现，自成立以来未现金分红，优先股于上市时转为普通股。
""",
]



def _load_latest_annual_chunks(agent: Agent, stock: str, market: str, root: Path) -> list[str]:
    """加载最新一份年报，选择相关章节。"""
    mds = sorted((root / market / stock / "derived").glob("*.md"))
    if not mds:
        raise SystemExit(f"{root / market / stock / 'derived'} 没有年报 md，先跑 ocr/cli.py")
    md_path = mds[-1]

    toc = Toc(agent, md_path)
    logger.info(f"阅读: {md_path.name}")

    pick_task = "你在通读一份上市公司年报的目录。大致了解企业商业过程，不分析财务，需要阅读哪些章节？"
    picked = toc.pick_chapters(toc.chapters, pick_task)

    logger.info(f"选中章节: {[toc.chapters[i]['title'] for i in picked]}")
    chunks = [chunk for i in picked for chunk in toc.chapter_text_chunk(i)]
    return chunks


def gen_business(agent: Agent, stock: str, market: str, root: Path) -> str:
    # 商业模式分析只需要最新一份年报
    chunks = _load_latest_annual_chunks(agent, stock, market, root)

    logger.info("gen …")

    prompt = textwrap.dedent(f"""
        <requirement>
        你在阅读一份上市公司年报，要把商业模式讲清楚，为了方便用户跨公司快速阅读：第一段「定位与产品」；第二段「经营模式」；第三段「客户」；第四段「资本」

        - 只写原文有依据的内容，禁止编造任何原文不存在的细节
        - 全文控制在1000字以内，语言用简体中文, markdown格式
        </requirement>

        <examples>
        {"\n".join(f"<example>{e}</example>" for e in _intro_examples)}
        </examples>""").strip()

    intro = agent.chat_reduce_chunks(prompt, chunks)

    if not intro.strip() :
        raise ValueError(f"{stock} {market} business 分析异常")

    return intro


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S", stream=sys.stderr)
    parser = argparse.ArgumentParser(description="business 模块：总结生意过程")
    parser.add_argument("--stock", default="601633")
    parser.add_argument("--market", default="cn", choices=["cn", "hk"])
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1] / ".cagent"))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--render", action="store_true", help="生成后渲染 business.html")
    args = parser.parse_args()

    output_dir = Path(__file__).resolve().parents[1] / "output" / "wiki" / f"{args.market}" / f"{args.stock}" 
    output_dir.mkdir(parents=True, exist_ok=True)

    md_output_path = output_dir / "business.md"
    if md_output_path.exists() and not args.force:
        doc = md_output_path.read_text(encoding="utf-8")

    else:
        root = Path(args.root)
        agent = Agent(trace=True)
        doc = gen_business(agent, args.stock, args.market, root)
        md_output_path.write_text(doc, encoding="utf-8")

    print(doc)

    if args.render:
        html_output_path = output_dir / "business.html"
        html_output_path.write_text(render_doc(doc, title=f"{args.stock} · 生意模式", ticker=args.stock), encoding="utf-8")
        logger.info(f"渲染: {html_output_path}")


if __name__ == "__main__":
    main()
