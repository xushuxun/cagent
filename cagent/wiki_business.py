"""business 模块：生意过程 writer

通读指定年份年报（默认最新），蒸馏这门生意如何运转。分两小节：业务说明（业务产品、
研发、采购、生产、销售、客户、资金流）与经营过程（HTML 链条 + 资金回流）。
产物落位 knowledge/fy<year>/business.json。

用法：
    uv run cagent/wiki_business.py --stock 601633 --market cn
    uv run cagent/wiki_business.py --stock 601633 --market cn --force
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


_intro_examples = [
    """### 业务说明
<div class="prose">
<p>沐曦股份是一家 Fabless 模式的 GPU 芯片设计公司，主要从事应用于人工智能训练和推理的 GPU 产品的研发、设计和销售，并配套自研软件栈。</p>
<p>公司的研发模式为自主研发，研发支出以研发人员薪酬为主，另有 EDA 软件、IP 授权和光罩的折旧摊销。公司的采购模式为根据生产计划和安全库存，向晶圆代工厂采购定制晶圆，向供应商采购 HBM 存储器及封装测试、板卡加工服务，对关键物料采取预付货款方式锁定货源。公司采用 Fabless 生产模式，晶圆流片、封装测试和板卡集成全部委托外部厂商完成，自身辅以工艺管理和测试支持；为应对供应链风险，公司进行战略备货。公司的销售模式为直销与经销相结合：直销模式下参与客户公开招标或商务谈判，签订销售合同后按订单发货，收取一定比例预收款，在信用期内形成应收账款；部分客户按照"背靠背"方式结算，即在收到最终用户项目回款后向公司付款。交付环节，部分客户要求公司提供集成多张 GPU 板卡的服务器整机。售后环节，公司向客户提供技术支持，向经销商提供技术培训。</p>
<p>公司的直接客户为服务器厂商、系统集成商和智算中心建设方（如新华三、超讯通信等），终端应用于教科研、金融、交通、能源等行业及国家人工智能公共算力平台。公司客户集中度较高，前五大客户收入占比长期在七成以上；经销模式借助经销商的区域和行业资源扩大终端覆盖；公司收入几乎全部来自中国内地。</p>
<p>资金流方面，公司的资金来源为客户预收款与回款，以及股权融资（上市前多轮增资和 IPO 募资，为报告期内最大的资金来源）；资金流出主要为向上游支付的晶圆和 HBM 预付款、研发人员薪酬以及光罩、IP 授权、EDA 软件等资本开支。公司收入集中在四季度确认且部分客户回款滞后，而上游采购需大额预付并备货，经营活动现金流量净额报告期各年均为负，资金缺口全部由股权融资弥补。公司处于重投入阶段，报告期内未进行现金分红，IPO 募集资金主要投向后续两代 GPU 产品的研发及产业化。</p>
</div>

### 经营过程
<div class="chain">
<div class="step"><b>研发</b>核心技术自研，支出以人员薪酬为主</div>
<div class="to">→</div>
<div class="step"><b>采购</b>晶圆与 HBM 预付锁货，战略备货</div>
<div class="to">→</div>
<div class="step"><b>生产</b>Fabless 模式，流片封测全委外</div>
<div class="to">→</div>
<div class="step"><b>销售</b>直销与经销结合，收预收款发货</div>
<div class="to">→</div>
<div class="step"><b>交付</b>板卡与整机交付，信用期形成应收</div>
<div class="to">→</div>
<div class="step"><b>回款</b>部分客户背靠背结算</div>
</div>
<div class="return">↩ 资金回流：回款与股权融资覆盖采购、薪酬与资本开支，支撑下一代产品研发</div>""",


    """### 业务说明
<div class="prose">
<p>MiniMax 是一家全球化的大模型公司，自研全模态大模型（文本、视频、语音、音乐、图像），并以此运营 AI 原生应用和面向企业与开发者的开放平台。</p>
<p>公司的研发模式为自主研发，研发支出主要为训练所用的云算力费用和研发人员薪酬，全部内部开发，不依赖外包或第三方技术授权。公司的采购模式为向云基础设施供应商租用 GPU 算力，轻资产运营、不持有硬件，部分协议约定最低采购量，付款信用期为 30-90 天；另采购数据标注、内容审核和营销推广服务。产品化环节，公司将模型封装为 AI 原生应用（海螺AI、Talkie/星野等）和开放平台 API，云端交付、无实体物流。销售环节，C 端通过自有网页端和应用商店上架，采取订阅制定价，以自然传播方式获客；B 端采取直销与渠道伙伴相结合，API 按调用量计价，企业客户签订框架协议并提供专属推理资源池。交付环节为在线调用即完成交付。售后环节为云服务运维、内容审核和多法域合规更新。</p>
<p>公司的客户为全球个人用户（累计超 2 亿）、企业与开发者（付费客户约 2,500 家）以及在应用内投放广告的第三方广告平台；收入约七成来自中国内地以外地区。</p>
<p>资金流方面，公司的资金来源为个人用户订阅和应用内充值（预付）、广告平台投放费、企业客户按量月结及许可费，以及优先股融资和 IPO 募资；资金流出主要为付给云厂商的训练与推理算力费用（最大支出）、员工薪酬和营销开支。公司对客户收款快于对云厂商付款（应收账款周转约 40-50 天，应付账款周转约 70-90 天）。公司不持有硬件，再投入主要为训练算力和研发人员而非固定资产；研发开支远大于收入，经营活动现金流持续为负，资金缺口由优先股融资弥补（优先股账面计为负债，上市时转为权益）；公司成立以来未分红，招股书披露的现金储备可支撑约三到四年运营。</p>
</div>

### 经营过程
<div class="chain">
<div class="step"><b>研发</b>全模态模型自研，算力与薪酬投入</div>
<div class="to">→</div>
<div class="step"><b>采购</b>租用云厂商 GPU 算力，信用期 30-90 天</div>
<div class="to">→</div>
<div class="step"><b>产品化</b>封装为应用与开放平台 API</div>
<div class="to">→</div>
<div class="step"><b>销售</b>C 端订阅，B 端按量计价</div>
<div class="to">→</div>
<div class="step"><b>交付</b>云端调用即交付</div>
<div class="to">→</div>
<div class="step"><b>回款</b>订阅预付、企业月结</div>
</div>
<div class="return">↩ 资金回流：订阅与按量回款覆盖算力与薪酬，缺口由融资弥补</div>""",
]



def _intro_prompt() -> str:
    return textwrap.dedent(f"""
        <requirement>
        你在阅读一份上市公司年报，要把这门生意的运行过程讲清楚，输出两个 ### 小节：
        业务说明（<div class="prose"> 里若干 <p>，覆盖业务产品、研发、采购、生产、销售、客户、资金流）；
        经营过程（<div class="chain"> 里若干 <div class="step"><b>步骤名</b>一句话注记</div>，
        步骤之间插 <div class="to">→</div>，从投入到回款；随后一行 <div class="return"> 写资金回流一句话）

        - 只描述生意如何运转，禁止分析财务数据、评价竞争力；不输出页码和章节名
        - 全文控制在1000字以内，语言用简体中文
        </requirement>

        <examples>
        {"\n".join(f"<example>{e}</example>" for e in _intro_examples)}
        </examples>""").strip()



def _load_latest_annual(agent: Agent, stock: str, market: str, root: Path) -> tuple[int, Toc, list[int], list[str]]:
    """加载最新一份年报，选择相关章节。返回 (year, toc, picked, chunks)。"""
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
    return report_year(agent, md_path), toc, picked, chunks


def gen_business(agent: Agent, stock: str, market: str, root: Path, force: bool) -> tuple[int, str]:
    # 商业模式分析只需要最新一份年报
    year, toc, picked, chunks = _load_latest_annual(agent, stock, market, root)

    out_path = root / market / stock / "knowledge" / f"fy{year}" / "business.json"
    if out_path.exists() and not force:
        logger.info(f"跳过（已存在）: {out_path}")
        return year, json.loads(out_path.read_text(encoding="utf-8"))["doc"]

    logger.info("gen …")

    intro = agent.chat_reduce_chunks(_intro_prompt(), chunks)

    if not intro.strip() :
        raise ValueError(f"{year} business 分析异常")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps({"year": year, "topic": "business", "source": toc.gen_source(picked), "doc": intro}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    logger.info(f"输出: {out_path}")
    return year, intro


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S", stream=sys.stderr)
    parser = argparse.ArgumentParser(description="business 模块：蒸馏生意过程，产物 knowledge/fy<year>/business.json")
    parser.add_argument("--stock", default="601633")
    parser.add_argument("--market", default="cn", choices=["cn", "hk"])
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1] / ".cagent"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    agent = Agent(trace=True)
    gen_business(agent, args.stock, args.market, Path(args.root), args.force)

    from cagent.wiki_render import render_module

    html_path = render_module(Path(args.root), args.market, args.stock, "business", Path("output/wiki") / f"{args.market}_{args.stock}" / "business.html")
    logger.info(f"渲染: {html_path}")


if __name__ == "__main__":
    main()
