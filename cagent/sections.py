"""sections：叙述节（与问题层同一套接口与纪律）

问题层的证据是程序算好的表；叙述节的证据是年报原文（公司介绍、管理层讨论），
但两者输出同一份东西：**可被程序校验的正文**。

- 带财务证据的叙述节：正文里的数字只许来自证据表（`checks.grounded_check`），越界就带反馈重写；
- 引用年报原文的地方标注页码，页码必须落在这一节读过的片段里；
- 一节可以有几个面（如"管理层判断"与"当年决策"），逐年写的节按年份组织。

用法：
    from cagent.sections import SECTIONS, write_section
    doc = write_section(agent, BUSINESS, stock, market, root, evidence_text)
"""

import logging
import re
import textwrap
from dataclasses import dataclass
from pathlib import Path

from cagent.checks import grounded_check
from cagent.chunk import Toc, report_year
from cagent.facts import _fit_chunks
from cagent.llm import LLM

logger = logging.getLogger(__name__)

_PAGE_MARK = re.compile(r"<!-- page (\d+) -->")
_PAGE_CITE = re.compile(r"p\.\s?(\d+)")


@dataclass(frozen=True)
class Section:
    """一节叙述：读哪些章节、写哪几个面、要不要带财务证据。"""

    id: str
    title: str
    pick_task: str                      # 章节检索任务
    parts: tuple[tuple[str, str], ...]  # (小标题, 只写这个面的要求)
    examples: tuple[str, ...] = ()
    per_report: bool = False            # 逐年写（读每一份年报）
    with_evidence: bool = False         # 带上财务证据，正文数字受校验
    max_tokens: int = 1600


def _fit_section_chunks(agent: LLM, chunks: list[str]) -> list[str]:
    return _fit_chunks(agent, chunks)


def _cited_pages(text: str) -> set[int]:
    return {int(match) for match in _PAGE_CITE.findall(text)}


def _write_part(agent: LLM, section: Section, title: str, instruction: str, chunks: list[str], evidence_text: str = "") -> str:
    examples = "\n".join(f"<example>{item}</example>" for item in section.examples)
    prompt = textwrap.dedent(f"""
        <requirement>
        {instruction}

        - 只写原文有依据的内容，禁止编造原文不存在的细节；去掉管理层吹嘘话术，语言简洁凝炼
        - 引用年报里的具体说法时标注页码，写成「（见年报 p.N）」
        - 用简体中文，markdown 格式
        </requirement>
        """ + (f"\n<examples>\n{examples}\n</examples>" if examples else "")).strip()
    if evidence_text and section.with_evidence:
        prompt = f"{prompt}\n\n<evidence>\n{evidence_text}\n</evidence>"
    if evidence_text and section.with_evidence:
        prompt += "\n\n需要量化时只引用 <evidence> 里的数字，数字照抄；不得自行计算或外推。"

    text = agent.distill(prompt, chunks)

    if evidence_text and section.with_evidence:
        # 叙述节的数字有两个合法来源：读过的年报原文，与给它的财务证据；
        # 页码必须落在读过的片段里——出问题就把具体位置反馈回去重写一次
        allowed_text = "\n".join(chunks) + "\n" + evidence_text
        problems = list(grounded_check(text, allowed_text).missing)
        available_pages = {int(match) for match in _PAGE_MARK.findall("\n".join(chunks))}
        bad_pages = sorted(page for page in _cited_pages(text) if available_pages and page not in available_pages)
        if bad_pages:
            problems.append("页码不在读过的片段里：" + "、".join(f"p.{page}" for page in bad_pages[:3]))
        if problems:
            logger.warning(f"{section.id} {title}：{len(problems)} 处越界，反馈重写")
            text = agent.distill(f"{prompt}\n\n<violations>\n上一稿有这些问题：{'；'.join(problems)}\n只许用读过的原文与给定证据里的数重写一遍。\n</violations>", chunks)
            remaining = list(grounded_check(text, allowed_text).missing)
            if remaining:
                logger.warning(f"{section.id} {title}：重写后仍有 {len(remaining)} 处越界：{'、'.join(remaining[:5])}")
    return text.strip()


def write_section(agent: LLM, section: Section, stock: str, market: str, root: Path, evidence_text: str = "") -> str:
    """写一节叙述：检索章节 → 逐面蒸馏 → （带证据时）校验正文数字。"""
    derived = root / market / stock / "derived"
    mds = sorted(derived.glob("*.md"))
    if not mds:
        raise SystemExit(f"{derived} 没有年报 md，先跑 ocr/cli.py")
    reports = {report_year(agent, path): path for path in mds}
    ordered = sorted(reports.items())
    picked_reports = ordered[-5:] if section.per_report else [ordered[-1]]

    parts = [f"# {section.title}\n"]
    for year, md_path in picked_reports:
        toc = Toc(agent, md_path)
        picked = toc.pick_chapters(toc.chapters, section.pick_task)
        chunks = _fit_section_chunks(agent, [chunk for index in picked for chunk in toc.chapter_text_chunk(index)])
        logger.info(f"{section.id} {year}: 章节 {[toc.chapters[i]['title'] for i in picked]} / 片段 {len(chunks)}")
        if section.per_report:
            parts.append(f"## {year}\n")
        for title, instruction in section.parts:
            body = _write_part(agent, section, title, instruction, chunks, evidence_text)
            if not body:
                raise ValueError(f"{section.id} {year} {title} 生成异常")
            parts.append(f"**{title}**\n\n{body}\n" if section.per_report else f"{body}\n")
    return "\n".join(parts).strip() + "\n"


def _business_instruction() -> str:
    return (
        "你在阅读一份上市公司年报，要把商业模式讲清楚，只关注核心生意过程机制，去掉报告期短期信息。"
        "说清：钱从哪来（客户与收入结构）、钱花在哪（成本与投入结构）、靠什么赚（价值链上的位置与壁垒）。"
        "关键投入（研发、产能、渠道）可以用财务证据里的数字量化。全文控制在 400 字以内。"
    )


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



BUSINESS = Section(
    id="business",
    title="生意模式",
    pick_task="找出公司介绍和管理层讨论分析章节",
    parts=(("生意模式", _business_instruction()),),
    examples=tuple(_intro_examples),
    with_evidence=True,
)

def _judge_instruction() -> str:
    return "你在阅读一份上市公司年报，提取管理层对行业环境的判断。全文控制在 500 字以内。"


def _decide_instruction() -> str:
    return "你在阅读一份上市公司年报，提取管理层的重大经营决策。全文控制在 500 字以内。"


DECISIONS = Section(
    id="decisions",
    title="历年经营决策",
    pick_task="找出管理层和董事会分析讨论相关章节",
    parts=(("管理层对行业大环境的判断", _judge_instruction()), ("当年的重大经营决策", _decide_instruction())),
    examples=tuple(_judge_examples + _decide_examples),
    per_report=True,
    with_evidence=False,
)

SECTIONS: tuple[Section, ...] = (BUSINESS, DECISIONS)
SECTION_BY_ID = {section.id: section for section in SECTIONS}
