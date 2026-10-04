"""business 模块：生意过程 writer

通读指定年份年报（默认最新），蒸馏这门生意如何运转：业务产品、研发、采购、生产、
销售、客户、资金流，以及经营过程主链条与资金回流。产物落位 knowledge/fy<year>/business.json。
"""

import json
import logging
import textwrap
from pathlib import Path

from cagent.agent import Agent, chunk_text
from cagent.chunk import load_tocs, script
from cagent.kdoc import SCHEMA

logger = logging.getLogger(__name__)

fewshot_outputs = [
    {
        "updated": True,
        "doc": """沐曦股份是一家 Fabless 模式的 GPU 芯片设计公司，主要从事应用于人工智能训练和推理的 GPU 产品的研发、设计和销售，并配套自研软件栈。

公司的研发模式为自主研发，研发支出以研发人员薪酬为主，另有 EDA 软件、IP 授权和光罩的折旧摊销。公司的采购模式为根据生产计划和安全库存，向晶圆代工厂采购定制晶圆，向供应商采购 HBM 存储器及封装测试、板卡加工服务，对关键物料采取预付货款方式锁定货源。公司采用 Fabless 生产模式，晶圆流片、封装测试和板卡集成全部委托外部厂商完成，自身辅以工艺管理和测试支持；为应对供应链风险，公司进行战略备货。公司的销售模式为直销与经销相结合：直销模式下参与客户公开招标或商务谈判，签订销售合同后按订单发货，收取一定比例预收款，在信用期内形成应收账款；部分客户按照"背靠背"方式结算，即在收到最终用户项目回款后向公司付款。交付环节，部分客户要求公司提供集成多张 GPU 板卡的服务器整机。售后环节，公司向客户提供技术支持，向经销商提供技术培训。

公司的直接客户为服务器厂商、系统集成商和智算中心建设方（如新华三、超讯通信等），终端应用于教科研、金融、交通、能源等行业及国家人工智能公共算力平台。公司客户集中度较高，前五大客户收入占比长期在七成以上；经销模式借助经销商的区域和行业资源扩大终端覆盖；公司收入几乎全部来自中国内地。

资金流方面，公司的资金来源为客户预收款与回款，以及股权融资（上市前多轮增资和 IPO 募资，为报告期内最大的资金来源）；资金流出主要为向上游支付的晶圆和 HBM 预付款、研发人员薪酬以及光罩、IP 授权、EDA 软件等资本开支。公司收入集中在四季度确认且部分客户回款滞后，而上游采购需大额预付并备货，经营活动现金流量净额报告期各年均为负，资金缺口全部由股权融资弥补。公司处于重投入阶段，报告期内未进行现金分红，IPO 募集资金主要投向后续两代 GPU 产品的研发及产业化。

### 经营过程

```mermaid
flowchart LR
    研发["研发<br>核心技术自研，支出以人员薪酬为主"] --> 采购["采购<br>晶圆与 HBM 预付锁货，战略备货"] --> 生产["生产<br>Fabless 模式，流片封测全委外"] --> 销售["销售<br>直销与经销结合，收预收款发货"] --> 交付["交付<br>板卡与整机交付，信用期形成应收"] --> 回款["回款<br>部分客户背靠背结算"]
```

资金回流：收入集中四季度确认、回款滞后，经营现金流持续为负，资金缺口由股权融资弥补。""",
    },
    {
        "updated": True,
        "doc": """MiniMax 是一家全球化的大模型公司，自研全模态大模型（文本、视频、语音、音乐、图像），并以此运营 AI 原生应用和面向企业与开发者的开放平台。

公司的研发模式为自主研发，研发支出主要为训练所用的云算力费用和研发人员薪酬，全部内部开发，不依赖外包或第三方技术授权。公司的采购模式为向云基础设施供应商租用 GPU 算力，轻资产运营、不持有硬件，部分协议约定最低采购量，付款信用期为 30-90 天；另采购数据标注、内容审核和营销推广服务。产品化环节，公司将模型封装为 AI 原生应用（海螺AI、Talkie/星野等）和开放平台 API，云端交付、无实体物流。销售环节，C 端通过自有网页端和应用商店上架，采取订阅制定价，以自然传播方式获客；B 端采取直销与渠道伙伴相结合，API 按调用量计价，企业客户签订框架协议并提供专属推理资源池。交付环节为在线调用即完成交付。售后环节为云服务运维、内容审核和多法域合规更新。

公司的客户为全球个人用户（累计超 2 亿）、企业与开发者（付费客户约 2,500 家）以及在应用内投放广告的第三方广告平台；收入约七成来自中国内地以外地区。

资金流方面，公司的资金来源为个人用户订阅和应用内充值（预付）、广告平台投放费、企业客户按量月结及许可费，以及优先股融资和 IPO 募资；资金流出主要为付给云厂商的训练与推理算力费用（最大支出）、员工薪酬和营销开支。公司对客户收款快于对云厂商付款（应收账款周转约 40-50 天，应付账款周转约 70-90 天）。公司不持有硬件，再投入主要为训练算力和研发人员而非固定资产；研发开支远大于收入，经营活动现金流持续为负，资金缺口由优先股融资弥补（优先股账面计为负债，上市时转为权益）；公司成立以来未分红，招股书披露的现金储备可支撑约三到四年运营。

### 经营过程

```mermaid
flowchart LR
    研发["研发<br>全模态模型自研，算力与薪酬投入"] --> 采购["采购<br>租用云厂商 GPU 算力，信用期 30-90 天"] --> 产品化["产品化<br>封装为应用与开放平台 API"] --> 销售["销售<br>C 端订阅，B 端按量计价"] --> 交付["交付<br>云端调用即交付"] --> 回款["回款<br>订阅预付、企业月结"]
```

资金回流：对客户收款快于对云厂商付款，经营现金流为负，缺口由优先股融资弥补。""",
    },
]

REQUIREMENTS = """你在通读一份上市公司年报，要把这门生意的运行过程讲清楚
把"待阅读的年报页"里的信息合并进 <current> 的文档，返回整合后的完整版本。

规则：
- 只描述生意如何运转：业务产品、研发、采购、生产、销售、客户、资金流
- 禁止分析财务数据、评价竞争力；不解释过程，不输出页码和章节名
- 每个事实必须能在 <pages> 或 <current> 中找到依据，禁止编撰
- 业务说明控制在1000字以内；经营过程用 mermaid flowchart LR 代码块表达，每步名称加一句话注记，步骤从投入到回款；确实不适合画流程的也可以改用有序列表
- 没有新信息时 updated=false，doc 原样返回
- 输出文档的语言（简繁体、用词）与 <pages> 保持一致
- 比例与占比照抄报告披露值，禁止改写成"不足X%"这类放宽表述

<examples>
""" + "\n\n".join(f"<example>\n<output>\n{json.dumps(output, ensure_ascii=False)}\n</output>\n</example>" for output in fewshot_outputs) + """
</examples>"""

PICK_TASK = "你在通读一份上市公司年报的目录。大致了解企业商业过程，不分析财务，需要阅读哪些章节？"


def gen_business(agent: Agent, stock: str, market: str, root: Path, force: bool, years: list[int] | None = None) -> None:
    tocs = load_tocs(root, market, stock)
    year = max(years) if years else max(tocs)
    toc = tocs[year]
    out_path = root / market / stock / "knowledge" / f"fy{year}" / "business.json"
    if out_path.exists() and not force:
        logger.info(f"跳过（已存在）: {out_path}")
        return

    logger.info(f"{year} …")
    lang = script(toc.text)
    picked = agent.pick_chapters(toc.chapters, PICK_TASK)
    logger.info(f"  选中章: {[toc.chapters[i]['title'] for i in picked]}")
    state = {"doc": ""}
    for i in picked:
        for chunk in chunk_text(toc.chapter_text(i)):
            prompt = textwrap.dedent(f"""
                原文是{lang}，doc 必须用{lang}撰写。

                {REQUIREMENTS}

                <current>
                {json.dumps(state, ensure_ascii=False)}
                </current>

                <pages>
                {chunk}
                </pages>
            """).strip()
            out = agent.chat_json(prompt, SCHEMA)
            if out.pop("updated", True) and out.get("doc", "").strip():
                state = out
    if not state["doc"].strip():
        raise ValueError(f"fy{year} 生意过程文档为空")

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
        json.dumps({"fy": year, "topic": "business", "source": source, "doc": state["doc"]}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    logger.info(f"输出: {out_path}")
