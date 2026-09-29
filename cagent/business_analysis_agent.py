import argparse
import json
import re
import textwrap
from pathlib import Path

from cagent.agent import Agent
from cagent.chunk import Toc

llm_agent = Agent(trace=True)

fewshots = [
        """
        沐曦股份是一家 Fabless 模式的 GPU 芯片设计公司，主要从事应用于人工智能训练和推理的 GPU 产品的研发、设计和销售，并配套自研软件栈。

        公司的研发模式为自主研发，研发支出以研发人员薪酬为主，另有 EDA 软件、IP 授权和光罩的折旧摊销。公司的采购模式为根据生产计划和安全库存，向晶圆代工厂采购定制晶圆，向供应商采购 HBM 存储器及封装测试、板卡加工服务，对关键物料采取预付货款方式锁定货源。公司采用 Fabless 生产模式，晶圆流片、封装测试和板卡集成全部委托外部厂商完成，自身辅以工艺管理和测试支持；为应对供应链风险，公司进行战略备货。公司的销售模式为直销与经销相结合：直销模式下参与客户公开招标或商务谈判，签订销售合同后按订单发货，收取一定比例预收款，在信用期内形成应收账款；部分客户按照"背靠背"方式结算，即在收到最终用户项目回款后向公司付款。交付环节，部分客户要求公司提供集成多张 GPU 板卡的服务器整机。售后环节，公司向客户提供技术支持，向经销商提供技术培训。

        公司的直接客户为服务器厂商、系统集成商和智算中心建设方（如新华三、超讯通信等），终端应用于教科研、金融、交通、能源等行业及国家人工智能公共算力平台。公司客户集中度较高，前五大客户收入占比长期在七成以上；经销模式借助经销商的区域和行业资源扩大终端覆盖；公司收入几乎全部来自中国内地。

        资金流方面，公司的资金来源为客户预收款与回款，以及股权融资（上市前多轮增资和 IPO 募资，为报告期内最大的资金来源）；资金流出主要为向上游支付的晶圆和 HBM 预付款、研发人员薪酬以及光罩、IP 授权、EDA 软件等资本开支。公司收入集中在四季度确认且部分客户回款滞后，而上游采购需大额预付并备货，经营活动现金流量净额报告期各年均为负，资金缺口全部由股权融资弥补。公司处于重投入阶段，报告期内未进行现金分红，IPO 募集资金主要投向后续两代 GPU 产品的研发及产业化。
        """,
        """
        MiniMax 是一家全球化的大模型公司，自研全模态大模型（文本、视频、语音、音乐、图像），并以此运营 AI 原生应用和面向企业与开发者的开放平台。

        公司的研发模式为自主研发，研发支出主要为训练所用的云算力费用和研发人员薪酬，全部内部开发，不依赖外包或第三方技术授权。公司的采购模式为向云基础设施供应商租用 GPU 算力，轻资产运营、不持有硬件，部分协议约定最低采购量，付款信用期为 30-90 天；另采购数据标注、内容审核和营销推广服务。产品化环节，公司将模型封装为 AI 原生应用（海螺AI、Talkie/星野等）和开放平台 API，云端交付、无实体物流。销售环节，C 端通过自有网页端和应用商店上架，采取订阅制定价，以自然传播方式获客；B 端采取直销与渠道伙伴相结合，API 按调用量计价，企业客户签订框架协议并提供专属推理资源池。交付环节为在线调用即完成交付。售后环节为云服务运维、内容审核和多法域合规更新。

        公司的客户为全球个人用户（累计超 2 亿）、企业与开发者（付费客户约 2,500 家）以及在应用内投放广告的第三方广告平台；收入约七成来自中国内地以外地区。

        资金流方面，公司的资金来源为个人用户订阅和应用内充值（预付）、广告平台投放费、企业客户按量月结及许可费，以及优先股融资和 IPO 募资；资金流出主要为付给云厂商的训练与推理算力费用（最大支出）、员工薪酬和营销开支。公司对客户收款快于对云厂商付款（应收账款周转约 40-50 天，应付账款周转约 70-90 天）。公司不持有硬件，再投入主要为训练算力和研发人员而非固定资产；研发开支远大于收入，经营活动现金流持续为负，资金缺口由优先股融资弥补（优先股账面计为负债，上市时转为权益）；公司成立以来未分红，招股书披露的现金储备可支撑约三到四年运营。
        """,
]


def pick_chapters(agent: Agent, toc_chapters: list[dict]) -> list[int]:
    """给 agent 章节列表，让它选出描述这门生意要精读的章。"""
    chapter_listing = "\n".join(
        f'<chapter index="{index}">\n{chapter["title"]}\n</chapter>'
        for index, chapter in enumerate(toc_chapters)
    )
    prompt = textwrap.dedent(f"""
        <requirements>
        你在通读一份上市公司年报的目录。大致了解企业商业过程，不分析财务，需要阅读哪些章节？
        从列表里选，返回章节序号。
        </requirements>

        <chapters>
        {chapter_listing}
        </chapters>
    """).strip()
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "chapters",
            "schema": {
                "type": "object",
                "properties": {"indexes": {"type": "array", "items": {"type": "integer"}}},
                "required": ["indexes"],
            },
        },
    }
    picked_indexes = json.loads(
        agent.chat(
            messages=[{"role": "user", "content": prompt}],
            response_format=response_format,
            temperature=0,
        )
    )["indexes"]
    picked_indexes = [index for index in picked_indexes if 0 <= index < len(toc_chapters)]
    if not picked_indexes:
        raise ValueError("模型没有返回有效章节序号")
    return picked_indexes


def chunk_text(text: str, budget: int = 32768) -> list[str]:
    """按字符预算切文本（约 2 字符/token，budget 取 n_ctx 即约 n_ctx/2 token）。
    切口优先落在 md 标题（行首 #）处，避免截断段落；标题间距超预算时退回硬切。"""
    heading_positions = [match.start() for match in re.finditer(r"(?m)^#", text)]
    chunks, chunk_start, previous_heading = [], 0, 0
    for position in heading_positions + [len(text)]:
        if position - chunk_start > budget:
            cut_position = previous_heading if previous_heading > chunk_start else position
            chunks.append(text[chunk_start:cut_position])
            chunk_start = cut_position
        previous_heading = position
    chunks.append(text[chunk_start:])
    return chunks


def revise(agent: Agent, business_description: str, new_pages: str) -> str:
    prompt = textwrap.dedent(f"""
        <requirements>
        你在通读一份上市公司年报，要把这门生意的运行过程讲清楚
        输入是“现有生意过程描述”和“待阅读的年报页”，返回整合后的新版描述

        规则：
        - 只描述生意如何运转：业务产品、研发、采购、生产、销售、客户、资金流
        - 禁止分析财务数据、评价竞争力；不解释过程，不输出页码和章节名
        - 每个事实必须能在 <pages> 或 <business_description> 中找到依据，禁止编撰
        - 没有新信息时 updated=false，description 原样返回 <business_description>
        - 参考 <examples>，长度控制在1000字以内
        </requirements>

        <examples>
        {"\n".join(f"<example>\n{example.strip()}\n</example>" for example in fewshots)}
        </examples>

        <business_description>
        {business_description}
        </business_description>

        <pages>
        {new_pages}
        </pages>
    """).strip()
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "revise",
            "schema": {
                "type": "object",
                "properties": {
                    "updated": {"type": "boolean"},
                    "description": {"type": "string"},
                },
                "required": ["updated", "description"],
            },
        },
    }
    response = json.loads(
        agent.chat(
            messages=[{"role": "user", "content": prompt}],
            response_format=response_format,
            temperature=0,
        )
    )
    return response["description"] if response["updated"] else business_description


def gen_business_description(agent: Agent, text: str, business_description: str = "") -> str:

    text_chunks = chunk_text(text)

    for chunk in text_chunks:
        business_description = revise(agent, business_description, chunk)

    return business_description


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="商业模式分析 agent")
    parser.add_argument(
        "-i",
        "--input",
        default=str(Path(__file__).resolve().parents[1] / ".cagent/cn/601633/derived/2026-03-28_1225047452.md"),
        help="年报 md 路径",
    )
    args = parser.parse_args()

    md_path = Path(args.input)
    toc = Toc(md_path)
    business_description = ""
    # for round_number in range(1, 4):
        # print(f"--- 第 {round_number} 轮 ---")

    picked_indexes = pick_chapters(llm_agent, toc.chapters)
    print(f"选中章: {[toc.chapters[i]['title'] for i in picked_indexes]}")

    for i in picked_indexes:
        business_description = gen_business_description(
            llm_agent,
            toc.chapter_text(i),
            business_description,
        )
        print("="*80)
        print(business_description)
        print("="*80)

