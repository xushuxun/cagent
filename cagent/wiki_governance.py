"""governance 模块：治理与激励 writer

通读指定年份年报（默认最新），蒸馏价值投资关心的"人"与制度：
谁掌权与关键人依赖、高管薪酬与股权激励计划、治理结构变化与股东权利、关联体系。
不罗列委员会名单与成员，不做员工构成表。产物落位 knowledge/fy<year>/governance.json。
"""

import json
import logging
import textwrap
from pathlib import Path

from cagent.agent import Agent, chunk_text
from cagent.chunk import load_tocs, script
from cagent.kdoc import SCHEMA

logger = logging.getLogger(__name__)

fewshots = [
    {
        "pages": """董事會目前由九名董事組成，其中包括四名執行董事，即閔俊傑博士、貧煒禕女士、趙鵬宇先生及周彧聰先生；兩名非執行董事，即陳英傑先生及劉偉先生；以及三名獨立非執行董事，即黃國濱先生、王鵬程博士及朱華星博士。閔俊傑博士擔任主席。本公司沒有區分主席及首席執行官，閔俊傑博士目前兼任這兩個職務。

2025年執行董事薪酬（合計薪酬，千美元）：貧煒禕女士3,223；閔俊傑博士241；趙鵬宇先生896；周彧聰先生1,318，合計5,678千美元。年內概無向非執行董事支付酬金。

首次公開發售前股份激勵計劃由本公司於2022年採納，根據該計劃授出的未行使購股權的相關A類普通股一直由僱員持股平台持有，上市後悉數歸屬不會對股東股權產生任何攤薄影響。本公司已向392名承授人授出購股權認購合共20,890,736股A類普通股。首次公開發售後股份激勵計劃自無條件之日起10年內有效，剩餘年期約九年零八個月。

由於本公司的股份在報告期內未在聯交所上市，董事出席股東大會的記錄將在本公司後續年度報告中披露。於截至2025年12月31日止年度，概無訂立或存續任何股權掛鉤協議，本公司並無購買、出售或贖回任何上市證券。

與關連方訂立持續關連交易：主API服務協議、阿里巴巴雲端服務協議及業務合作協議。""",
        "output": {
            "updated": True,
            "doc": """### 掌權與關鍵人

閔俊傑自2023年10月起任董事、首席執行官兼首席技術官，並兼任董事會主席，主席與CEO不分離；公司採用不同投票權架構。四名執行董事分掌整體戰略、運營、語言模型與視覺模型研發。

### 薪酬與激勵

2025年執行董事合計薪酬5,678千美元：貧煒禕3,223、周彧聰1,318、趙鵬宇896、閔俊傑241。2022年採納IPO前股份激勵計劃，向392名承授人授出購股權認購20,890,736股A類普通股，相關股份已預先發行予僱員持股平台，歸屬不攤薄現有股東；IPO後計劃剩餘年期約九年零八個月。

### 治理與股東權利

股份於報告期內未在聯交所上市，董事出席記錄將在後續年報披露；年內無股權掛鉛協議，無購回上市證券。

### 關聯體系

與阿里巴巴等關連方訂立持續關連交易（主API服務、雲端服務、業務合作）。""",
        },
    },
    {
        "pages": """姓名陈维良国籍中国是否取得其他国家或地区居留权否主要职业及职务沐曦集成电路(上海)股份有限公司法定代表人、董事长、总经理过去10年曾控股的境内外上市公司情况无。控股股东：上海骄迈企业咨询合伙企业(有限合伙)，单位负责人陈维良，属于持股平台。

报告期内从公司获得的税前薪酬总额（万元）：陈维良320.33、彭莉240.39、杨建227.42、王爽175.39、魏忠伟147.81。全体董事、高级管理人员和核心技术人员税前报酬合计1,449.86万元。

公司于2025年实施股权激励计划、员工持股计划或其他员工激励措施的情况：股权激励总体情况□适用 √不适用。公司于2026年3月25日召开董事会，审议通过了《关于2025年度利润分配方案的议案》，根据《公司章程》规定，公司本年度尚未盈利，尚不满足利润分配条件，因此公司2025年度拟不进行利润分配。""",
        "output": {
            "updated": True,
            "doc": """### 掌权与关键人

实际控制人为陈维良（法定代表人、董事长兼总经理），通过持股平台上海骄迈控制公司，过去十年无其他控股上市公司；核心管理层均为技术出身的内部提拔团队。

### 薪酬与激励

2025年全体董事、高管及核心技术人员税前报酬合计1,449.86万元，其中陈维良320.33万元、彭莉240.39万元、杨建227.42万元。报告期内无股权激励计划、员工持股计划。2025年度拟不进行利润分配：公司尚未盈利，不满足章程规定的分红条件。

### 治理与股东权利

公司为红筹架构，2025年3月资本公积转增股本，注册资本由1,473.96万元增至3.6亿元；现金分红政策明确中期分红上限不超过相应期间归母净利润，决策程序完备。

### 关联体系

保荐机构子公司华泰创新投资战略配售955,474股，2027年12月17日可上市交易。""",
        },
    },
]

REQUIREMENTS = """你在通读一份上市公司年报，要蒸馏对股东最重要的治理与激励事实
把 <pages> 里的信息合并进 <current> 的文档，返回整合后的完整版本。

规则：
- 四个 ### 小节，顺序：掌权与关键人、薪酬与激励、治理与股东权利、关联体系
- 掌权与关键人：实际控制人/控股股东是谁、关键人依赖、任职历史
- 薪酬与激励：核心高管薪酬（照抄带单位）；股权激励计划及其考核/归属条件、对股东的摊薄；分红政策与实际分配
- 治理与股东权利：治理结构变化（如监事会设置、特别投票权）、股东权利安排
- 关联体系：控股股东持股平台、体外公司、持续关连交易等影响股东实际利益的安排
- 不罗列委员会名单与成员，不做员工构成表
- 每个数字必须来自 <pages>，禁止编撰；没有新内容时 updated=false，doc 原样返回
- 输出文档的语言（简繁体、用词）与 <pages> 保持一致

<examples>
""" + "\n\n".join(f"<example>\n<pages>\n{x['pages']}\n</pages>\n<output>\n{json.dumps(x['output'], ensure_ascii=False)}\n</output>\n</example>" for x in fewshots) + """
</examples>"""

PICK_TASK = "你在通读一份上市公司年报的目录。了解公司治理与激励：实际控制人、高管薪酬、股权激励计划、分红政策、关联交易，需要阅读哪些章节？"


def gen_governance(agent: Agent, stock: str, market: str, root: Path, force: bool, years: list[int] | None = None) -> None:
    tocs = load_tocs(root, market, stock)
    year = max(years) if years else max(tocs)
    toc = tocs[year]
    out_path = root / market / stock / "knowledge" / f"fy{year}" / "governance.json"
    if out_path.exists() and not force:
        logger.info(f"跳过（已存在）: {out_path}")
        return

    logger.info(f"{year} …")
    lang = script(toc.text)
    picked = agent.pick_chapters(toc.chapters, PICK_TASK)
    logger.info(f"  选中章: {[toc.chapters[i]['title'] for i in picked]}")
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
    state = {"doc": ""}
    for i in picked:
        for chunk in chunk_text(toc.chapter_text(i)):
            prompt = textwrap.dedent(f"""
                原文是{lang}，doc 必须用{lang}撰写。

                {REQUIREMENTS}
                {example}

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
        raise ValueError(f"fy{year} 治理与激励文档为空")

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
        json.dumps({"fy": year, "topic": "governance", "source": source, "doc": state["doc"]}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    logger.info(f"输出: {out_path}")
