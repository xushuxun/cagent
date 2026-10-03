"""org 模块：公司人事组织架构 writer

通读指定年份年报（默认最新），蒸馏公司治理与人事组织：董事会与委员会、
经营层与业务条线、高管与简历、员工构成。产物落位 knowledge/fy<year>/org.json。
"""

import json
import logging
import textwrap
from pathlib import Path

from cagent.agent import Agent, chunk_text
from cagent.chunk import load_tocs

logger = logging.getLogger(__name__)

fewshots = [
    {
        "pages": """董事會目前由九名董事組成，其中包括四名執行董事，即閔俊傑博士、貧煒禕女士、趙鵬宇先生及周彧聰先生；兩名非執行董事，即陳英傑先生及劉偉先生；以及三名獨立非執行董事，即黃國濱先生、王鵬程博士及朱華星博士。閔俊傑博士擔任主席。閔俊傑博士（董事會主席、首席執行官兼首席技術官）；貧煒禕女士（總裁）；趙鵬宇先生（大語言模型研究及工程負責人）；周彧聰先生（視覺模型研究及工程負責人）。本公司已成立審計委員會、薪酬委員會、提名委員會及企業管治委員會。本公司沒有區分主席及首席執行官，閔俊傑博士目前兼任這兩個職務。

2025年執行董事薪酬（合計薪酬，千美元）：貧煒禕女士3,223；閔俊傑博士241；趙鵬宇先生896；周彧聰先生1,318。年內概無向非執行董事支付酬金。趙鵬宇先生及周彧聰先生自2025年6月起擔任本公司董事。

員工人數：全職415人，兼職3人；按性別劃分男性286人、女性129人；按年齡劃分30周歲以下264人、30-50周歲151人；按地區劃分華東地區276人、華北地區128人、其他地區11人。""",
        "output": {
            "updated": True,
            "tree": [
                {"cards": [{"title": "董事會 · 9 人", "body": "執行董事 4 · 非執行董事 2 · 獨立非執行董事 3", "top": True}]},
                {"cards": [
                    {"title": "審計委員會", "body": "財務報告與外部核數師監督"},
                    {"title": "薪酬委員會", "body": "董事及高級管理層薪酬政策與架構"},
                    {"title": "提名委員會", "body": "董事會構成、多元化與提名程序"},
                    {"title": "企業管治委員會", "body": "企業管治常規與不同投票權架構相關事務"},
                ]},
                {"cards": [{"title": "經營層", "body": "閔俊傑兼任主席、首席執行官及首席技術官；貧煒禕任總裁；趙鵬宇、周彧聰分掌語言與視覺模型研發", "top": True}]},
            ],
            "org_note": "公司採用不同投票權架構，主席與首席執行官不分離，由閔俊傑一人兼任；股份於報告期內未在聯交所上市。",
            "executives": [
                {"name": "閔俊傑", "role": "主席、首席執行官兼首席技術官", "duty": "2023年10月起任董事，負責整體戰略與技術方向", "pay": "241 千美元"},
                {"name": "貧煒禕", "role": "執行董事、總裁", "duty": "2022年12月起任首席運營官，2026年3月調任總裁，負責公司運營", "pay": "3,223 千美元"},
                {"name": "趙鵬宇", "role": "執行董事", "duty": "大語言模型研究及工程負責人，2025年6月起任董事", "pay": "896 千美元"},
                {"name": "周彧聰", "role": "執行董事", "duty": "視覺模型研究及工程負責人，2025年6月起任董事", "pay": "1,318 千美元"},
            ],
            "exec_note": "年內非執行董事陳英傑、劉偉不領取酬金；獨立非執行董事為黃國濱、王鵬程、朱華星。",
            "employees": [
                {"item": "全職僱員", "value": "415", "src": "兼職 3 人"},
                {"item": "30 周歲以下", "value": "264", "src": "佔全職僱員 63%", "hl": True},
                {"item": "女性僱員", "value": "129", "src": "佔 31%"},
                {"item": "華東地區", "value": "276", "src": "華北 128 · 其他 11"},
            ],
        },
    },
    {
        "pages": """公司董事会下设审计委员会、薪酬与考核委员会、提名委员会及战略委员会。现任董事：陈维良（董事长、总经理）、彭莉（董事、副总经理、首席技术官）、杨建（董事、副总经理、首席技术官）、陈阳（职工代表董事）、王爽（董事）、俞博文（董事）、李树华（独立董事）、刘辛蕊（独立董事）、朱琴（独立董事）。

报告期内从公司获得的税前薪酬总额（万元）：陈维良320.33、彭莉240.39、杨建227.42、陈阳125.66、王爽175.39、俞博文—、李树华11.25、刘辛蕊11.25、朱琴11.25；高级管理人员魏忠伟（财务负责人、董事会秘书）147.81。全体董事、高级管理人员和核心技术人员税前报酬合计1,449.86万元。陈维良，男，1976年8月出生，博士研究生，曾任超威半导体（上海）有限公司高级总监，2020年9月至今于公司任职，目前担任公司董事长、总经理。

在职员工的数量合计925人，其中研发人员675人，占公司总人数的73%。公司研发人员中博士研究生26人、硕士研究生446人、本科及以下203人。""",
        "output": {
            "updated": True,
            "tree": [
                {"cards": [{"title": "董事会 · 9 人", "body": "非独立董事 5 · 独立董事 3 · 职工董事 1；下设审计、薪酬与考核、提名、战略四个委员会", "top": True}]},
                {"cards": [{"title": "经营层", "body": "陈维良兼任董事长、总经理；魏忠伟任财务负责人兼董事会秘书", "top": True}]},
            ],
            "org_note": "公司为红筹架构；2025年3月资本公积转增股本，注册资本由1,473.96万元增至3.6亿元。",
            "executives": [
                {"name": "陈维良", "role": "董事长、总经理", "duty": "曾任超威半导体（上海）高级总监，2020年9月至今任职，2024年12月起任董事长", "pay": "320.33 万元"},
                {"name": "彭莉", "role": "董事、副总经理、首席技术官", "duty": "核心技术负责人之一，2024年12月起任现职", "pay": "240.39 万元"},
                {"name": "杨建", "role": "董事、副总经理、首席技术官", "duty": "核心技术负责人之一，2024年12月起任现职", "pay": "227.42 万元"},
                {"name": "魏忠伟", "role": "财务负责人、董事会秘书", "duty": "财务与资本运作，2024年12月起任现职", "pay": "147.81 万元"},
            ],
            "exec_note": "报告期内3名董事离任；全体董事、高管及核心技术人员税前报酬合计1,449.86万元。",
            "employees": [
                {"item": "在职员工", "value": "925", "src": "母公司及主要子公司", "hl": True},
                {"item": "研发人员", "value": "675", "src": "占公司总人数 73%"},
                {"item": "硕士及以上", "value": "472", "src": "博士 26 · 硕士 446"},
            ],
        },
    }
]

REQUIREMENTS = """你在通读一份上市公司年报，要整理公司治理与人事组织
把 <pages> 里的信息合并进 <current>，返回合并后的完整版本。
tree 是组织架构，分层卡片：title 一句（可含人数等结构事实），body 一句职责或构成，顶层卡片标 top；按 董事会→专门委员会→经营层→业务与职能条线 的层次。
executives 选 3-6 位最关键高管：name/role/duty（一句简历与分管）/pay（报告期内薪酬总额，照抄并带单位，如"566.18 万元""3,223 千美元"；未披露填"—"）。
employees 是员工构成行：item/value/src 照抄报告口径，hl 标出最值得注意的一行。
org_note 与 exec_note 各一两句制度事实（如监事会设置变化、任职历史），没有则留空串。
每个数字必须来自 <pages>，禁止编撰；没有新信息时 updated=false，各字段原样返回。"""

SCHEMA = {
    "type": "object",
    "properties": {
        "updated": {"type": "boolean"},
        "tree": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "cards": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "body": {"type": "string"},
                                "top": {"type": "boolean"},
                            },
                            "required": ["title", "body"],
                        },
                    }
                },
                "required": ["cards"],
            },
        },
        "org_note": {"type": "string"},
        "executives": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "role": {"type": "string"},
                    "duty": {"type": "string"},
                    "pay": {"type": "string"},
                },
                "required": ["name", "role", "duty", "pay"],
            },
        },
        "exec_note": {"type": "string"},
        "employees": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "item": {"type": "string"},
                    "value": {"type": "string"},
                    "src": {"type": "string"},
                    "hl": {"type": "boolean"},
                },
                "required": ["item", "value", "src"],
            },
        },
    },
    "required": ["updated", "tree", "org_note", "executives", "exec_note", "employees"],
}

PICK_TASK = "你在通读一份上市公司年报的目录。了解公司治理与人事组织：董事会与专门委员会构成、高级管理人员薪酬与简历、员工构成，需要阅读哪些章节？"


def gen_org(agent: Agent, stock: str, market: str, root: Path, force: bool, years: list[int] | None = None) -> None:
    tocs = load_tocs(root, market, stock)
    year = max(years) if years else max(tocs)
    toc = tocs[year]
    out_path = root / market / stock / "knowledge" / f"fy{year}" / "org.json"
    if out_path.exists() and not force:
        logger.info(f"跳过（已存在）: {out_path}")
        return

    logger.info(f"{year} …")
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
    state = {"tree": [], "org_note": "", "executives": [], "exec_note": "", "employees": []}
    for i in picked:
        for chunk in chunk_text(toc.chapter_text(i)):
            prompt = textwrap.dedent(f"""
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
            if out.pop("updated", True):
                state = out
    if not state["tree"] or not state["executives"]:
        raise ValueError(f"fy{year} 组织架构或高管为空")

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
        json.dumps({"fy": year, "topic": "org", "source": source, **state}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    logger.info(f"输出: {out_path}")
