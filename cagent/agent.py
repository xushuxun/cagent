"""单公司价值投资报告 agent。

入口是 cagent/report.py：`uv run python cagent/report.py --market hk --code 09863`，
它在自管的 llama-server 会话内（在跑复用、没有拉起）调用本模块的 main()；缺失的
.pageindex.json 由 agent 在会话内补建。环节验证（cagent/tests/）直接 import
本模块的各环节函数，自管服务。

流水线（全部产物内存串联，无缓存；TOC 是文档构建产物落盘数据目录）：
    accounting（AccountingBuilder）→ evidence（QualExtractor + EvidenceComposer）
    → report（LiquidationAssessor + 程序估值表 + ReportWriter + FidelityChecker）
数字由代码处理和验证、程序渲染表格占位符、对账只检测不改写。
"""

import argparse
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import dspy

from cagent.lib.accounting import AccountingBuilder, AccountingTable
from cagent.lib.lakehouse import LakehouseReader, page_failures
from cagent.lib.qual import build_qual
from cagent.lib.valuation import (
    LiquidationAssessor,
    anchor_text,
    build_financials,
    dcf_matrix_md,
)
from cagent.lib.verify import FidelityChecker
from cagent.llm import LLM_BASE, TracedLM, parse_json, tag, try_connect
from cagent.pageindex.builder import PageIndexBuilder
from cagent.pageindex.reader import pageindex_is_current

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("cagent")

REPORTS_ROOT = Path("reports")  # 报告产出根目录：<market>/<code>/report.md + meta.json
_ANNUAL_MARKERS = ("年報", "年报", "年度報告", "年度报告")


def _is_annual_title(title: str) -> bool:
    """OCR/下载层与报告层共用同一判定标准：标题含年报词，排除摘要/英文版等。"""
    t = (title or "").strip()
    tl = t.lower()
    if not t:
        return True
    if any(k in t for k in ("摘要", "英文", "English", "Abridged")) \
            or any(k in tl for k in ("english", "abridged")):
        return False
    return any(k in t for k in _ANNUAL_MARKERS) or "annual report" in tl


# 证据合并结果必须包含的顶层键（缺则重试一次；meta/narrative 由程序装配，不在其列）
_EVIDENCE_TOP_KEYS = {"integrity", "business", "fcf_analysis", "moat",
                      "management", "data_gaps"}


class NarrativeEvolution(dspy.Signature):
    """跨年综合历年"管理层叙事"，产出进化轨迹。只依据给定材料，禁止外部信息。
promises_vs_outcomes 逐年核对：每条承诺在后续年份的行动与财务结果里找回声，
找不到回声标"落空"，找不到后续证据标"待观察"；语气变化、认知进化或狭隘、
能力提高或平庸都要在轨迹中反映（引用具体年份）。"""

    narratives: str = dspy.InputField(desc="历年年报管理层叙事 JSON（逐年 entries）")
    evolution: str = dspy.OutputField(
        desc='JSON {"cognitive_track": "认知轨迹2-4句", "capability_track": '
             '"能力轨迹2-4句", "promises_vs_outcomes": [{"promise", "year", '
             '"outcome": "兑现/部分兑现/落空/待观察", "financial_echo", "evidence"}]}')


class MergeEvidence(dspy.Signature):
    """把历年公告章节摘要综合成结构化证据 JSON，供按模板生成报告。只装定性事实
（数字表格由程序从数字表直渲）。相同检查项保留最新状态并留历史变化线索；每个
关键结论带来源标注（年报YYYY，p.N）；缺失填 null/"未披露"，禁止编造；各份矛盾
列入 data_gaps；叙事引用财务数字以随附的财务事实基准（程序直解计算）为准。"""

    materials: str = dspy.InputField(desc="财务事实基准 + 历年公告章节摘要")
    evidence: str = dspy.OutputField(desc='''JSON（只含这些顶层键，meta/narrative 不要输出）：
{"integrity": {"audit_opinion", "restatements", "regulatory_cases",
 "controller_fund_usage", "going_concern", "major_related_party",
 "pledge_ratio", "red_flags": ["事项：证据"]},
 "business": {"one_sentence": "谁在付钱、为什么付钱、靠什么持续赚钱",
 "mechanism": ["客户是谁/为何付费", "转换代价", "集中度", "分部构成", "量价拆解",
 "收入确认", "成本大头", "扩张方式", "牌照补贴依赖", "关键经营指标"],
 "key_dependencies": [{"item", "material": true/false, "evidence"}]},
 "fcf_analysis": "自由现金流成因（每条带出处）：净利润→经营现金流缺口归因
 （折旧摊销、营运资金占用/释放方向与原因、一次性项目）；资本开支投向与
 维持性/扩张性判断；哪部分现金流可持续、哪部分不可",
 "moat": {"yearly": [{"year", "capex_direction", "key_operating_metric", "yoy",
 "management_quote": "管理层对竞争优势的表述原文", "source"}],
 "switching_cost_records": ["客户留存/提价记录：证据"]},
 "management": {"capital_allocation": [{"item", "time", "scale", "result", "source"}],
 "ownership_incentives": ["控股股东持股/激励/增减持/分红政策：证据"],
 "management_background": ["董事与核心高管履历：证据"]},
 "data_gaps": ["关键数据缺口、未解决的披露矛盾、待外部验证的问题"]}''')


class RenderReport(dspy.Signature):
    """把结构化证据 JSON 按模板写成最终 Markdown 报告（价值投资视角，写给人看，
言简意赅）。只摆可溯源的事实与记录，每条关键信息标来源（年报YYYY，p.N），不写
评价性结论，判断留给读者。模板中 {{fin_table}} {{earnings_check}} {{dcf_table}}
{{liquidation_table}} 是数字表格占位符：独占一行原样保留，禁止删除、改写或另画
表格。叙事引用财务数字必须与证据中 _accounting_md（程序直解计算）一致；_accounting_md
没有的经营数字（销量、补贴等）必须带页码出处。不使用股价、市值等市场数据；估值数字
只出现在占位符表格中。金额统一用报表主币种并注明单位，以合并报表为准；禁止提及
"证据JSON""占位符"等内部词汇。「管理层言行记录」以 narrative（yearly + evolution）
为骨干写连贯叙事：认知判断的演变（management_said/tone + cognitive_track，关键
判断附原文摘录）、承诺与兑现（promises/capital_plan 对照 actions，兑现情况以
promises_vs_outcomes 为准）、财务体现（capability_track + financial_echo）。「逐年
资本开支与经营指标」表的"管理层表述"列摘录 management_said 原文。「数据缺口」
逐条列 data_gaps，无缺口写"无重大缺口"。「盈利质量对账」表格之后按模板用
fcf_analysis 写自由现金流成因叙事。「财务体现」写收入/利润/现金流/资本回报走势的
连贯叙事（引用「财务数据」，注意区分累计与年均）。模板中 <!-- --> 注释是写作指导，
不得以任何形式复述进正文。"""

    materials: str = dspy.InputField(desc="报告模板 + 结构化证据 JSON")
    report: str = dspy.OutputField(desc="完整 Markdown 报告正文")


class DirectReport(dspy.Signature):
    """证据合并失败时的兜底：直接根据历年公告章节摘要按模板写报告。摘要没覆盖的
写"未披露"，禁止编造；数字表格占位符独占一行原样保留；不掌握实时股价与市值，
不输出股价/市值/PE/PB 与买卖建议。模板中 <!-- --> 包裹的是写作指导，不要抄进
报告正文。"""

    materials: str = dspy.InputField(desc="报告模板 + 历年公告章节摘要")
    report: str = dspy.OutputField(desc="完整 Markdown 报告正文，简体中文，结构遵循模板")


class EvidenceComposer(dspy.Module):
    """跨份合并证据 JSON（只装定性）+ 跨年综合管理层叙事。"""

    def __init__(self):
        super().__init__()
        self.merge = dspy.Predict(MergeEvidence)
        self.evolve = dspy.Predict(NarrativeEvolution)

    def forward(self, lm: TracedLM, code: str, market: str, quals: list[dict],
                anchor: str) -> dict | None:
        quals = [q for q in quals if q.get("summaries")]
        if not quals:
            return None
        summaries_text = "\n\n".join(
            f"## 第 {i} 份公告：{q['filing']}\n{q['summaries']}"
            for i, q in enumerate(quals, 1))
        materials = (
            f"请为股票代码 {code}（市场 {market}）综合结构化证据。\n\n"
            f"财务事实基准（程序从原文直解计算，叙事引用数字以此为准）：\n{anchor}\n\n"
            f"历年公告章节摘要：\n\n{summaries_text}"
        )
        for attempt in (1, 2):
            with tag("evidence"):
                pred = self.merge(materials=materials, lm=lm,
                                  config={"max_tokens": 8000})
            evidence = parse_json(pred.evidence)
            if isinstance(evidence, dict) and _EVIDENCE_TOP_KEYS.issubset(evidence):
                return evidence
            if isinstance(evidence, dict):
                missing = sorted(_EVIDENCE_TOP_KEYS - set(evidence))
                logger.warning("证据 JSON 缺顶层键 %s%s", missing,
                               "，重试一次" if attempt == 1 else "")
        logger.warning("证据合并失败")
        return None

    def compose_evolution(self, lm: TracedLM,
                          narratives: list[dict]) -> dict | None:
        """跨年综合：认知轨迹 / 能力轨迹 / 承诺兑现对账。"""
        if not narratives:
            return None
        with tag("narrative:evolution"):
            pred = self.evolve(
                narratives=json.dumps(narratives, ensure_ascii=False, indent=1),
                lm=lm, config={"max_tokens": 5000})
        reply = parse_json(pred.evolution)
        return reply if isinstance(reply, dict) else None


class ReportWriter(dspy.Module):
    """按模板渲染报告（正常渲染 + 证据缺失时的直接撰写兜底）。"""

    def __init__(self):
        super().__init__()
        self.render = dspy.Predict(RenderReport)
        self.direct = dspy.Predict(DirectReport)

    def forward(self, lm: TracedLM, template_text: str, evidence: dict,
                code: str, market: str) -> str:
        materials = (
            f"请为股票代码 {code}（市场 {market}）按模板撰写报告。\n\n"
            f"报告模板如下：\n\n{template_text}\n\n"
            f"结构化证据 JSON：\n\n{json.dumps(evidence, ensure_ascii=False, indent=2)}"
        )
        with tag("report"):
            pred = self.render(materials=materials, lm=lm,
                               config={"max_tokens": max(12000, len(template_text) // 2)})
        return _strip_think(pred.report or "")

    def direct_write(self, lm: TracedLM, template_text: str, code: str,
                     market: str, quals: list[dict]) -> str:
        summaries_text = "\n\n".join(f"## {q['filing']}\n{q.get('summaries', '')}"
                                     for q in quals)
        with tag("report"):
            pred = self.direct(
                materials=(
                    f"请为股票代码 {code}（市场 {market}）按模板撰写报告。\n\n"
                    f"报告模板如下：\n\n{template_text}\n\n"
                    f"历年公告章节摘要：\n\n{summaries_text}"
                ), lm=lm,
                config={"max_tokens": max(12000, len(template_text) // 2)})
        return _strip_think(pred.report or "")


def _strip_think(content: str) -> str:
    """去掉 reasoning 模型泄漏进 content 的 <think> 块。"""
    return re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()


@dataclass
class Ctx:
    lm: TracedLM
    reader: LakehouseReader
    market: str
    code: str
    company_name: str
    template_text: str
    output: Path
    derived: Path
    filings: list[dict]


def _company_name(data_dir: Path) -> str:
    """从 index.json 读取公司名，缺省返回代码本身。"""
    index = data_dir / "index.json"
    if index.exists():
        return json.loads(index.read_text(encoding="utf-8")).get("stockName", "")
    return ""


def prepare(lm: TracedLM, reader: LakehouseReader, market: str, code: str,
            template: Path, output: Path) -> Ctx:
    """装配任务状态：定位数据目录、公告清单与模板。"""
    data_dir = reader.company_dir(market, code)
    if not (data_dir / "derived").is_dir():
        raise SystemExit(f"数据目录不存在: {data_dir}/derived")
    filings = [f for f in reader.list_filings(data_dir)
               if _is_annual_title(f.get("title", ""))]
    if not filings:
        raise SystemExit(f"{data_dir}/derived 下没有年报 markdown（非年报产物已跳过）")
    return Ctx(
        lm=lm, reader=reader, market=market, code=code,
        company_name=_company_name(data_dir),
        template_text=template.read_text(encoding="utf-8"),
        output=output, derived=data_dir / "derived", filings=filings,
    )


def _ensure_tocs(ctx: Ctx) -> list[tuple[str, str]]:
    """为每份公告确保有最新 .pageindex.json 构建产物
    （缺失/损坏/与原文页数不一致才构建）；失败不中断，但返回告警。

    构建失败意味着这份公告进不了定性材料——只写日志的话，报告里就变成静默的
    「未披露」。accounting 直解全文表格，不需要页码索引。
    """
    builder = PageIndexBuilder(ctx.reader)
    alerts: list[tuple[str, str]] = []
    for i, f in enumerate(ctx.filings, 1):
        md = ctx.derived / f["file"]
        if pageindex_is_current(md, ctx.reader):
            continue
        logger.info("构建 TOC %d/%d: %s", i, len(ctx.filings), f["file"])
        try:
            builder.build(md)
        except SystemExit as e:  # 无页码标记 / 模型判断无目录 / 条目提取失败
            logger.info("跳过章节目录 %d/%d: %s（%s）", i, len(ctx.filings), f["file"], e)
            alerts.append((f["file"], f"章节目录未构建：{e}"))
        except Exception as e:
            logger.warning("TOC 构建失败 %d/%d: %s (%s)", i, len(ctx.filings),
                           f["file"], e)
            alerts.append((f["file"], f"章节目录构建失败：{e}"))
    return alerts


def _substitute_tables(content: str, tables: dict[str, str]) -> str:
    """占位符替换成程序生成的数字表格；被模型弄丢的占位符追加到文末并告警。"""
    for marker, md in tables.items():
        if marker in content:
            content = content.replace(marker, md)
        else:
            logger.warning("渲染丢失占位符 %s，表格追加到文末", marker)
            content += f"\n\n{md}\n"
    # 程序没给数字表的占位符（如数字表提取失败）不能原样留在报告里
    for marker in re.findall(r"\{\{[a-zA-Z_]+\}\}", content):
        content = content.replace(marker, "（数据不足，该数字表格未生成）")
    # 模板里 <!-- --> 是写给 AI 的指导，程序兜底删除（模型可能照抄）
    return re.sub(r"\n?<!--.*?-->\n?", "\n", content, flags=re.DOTALL)


def compute_valuation_tables(evidence: dict, acct: AccountingTable,
                             fin: dict) -> tuple[dict[str, str], list[str]]:
    """估值表格全部程序计算：DCF 敏感性矩阵 + 清算表（LLM 只出折扣率）。

    返回 (表格, 告警)；告警由调用方并入异常清单（披露替代修复）。
    """
    unit = f"亿{fin['currency']}" if fin.get("currency") else "亿（币种未标注）"
    meta = evidence.get("meta") or {}
    r = meta.get("required_return")
    r = float(r) if isinstance(r, (int, float)) and 0.03 <= float(r) <= 0.25 else 0.10
    oe = fin["owner_earnings"]
    dcf_md = dcf_matrix_md(oe["avg"]["net_profit"], oe["avg"]["free_cash_flow"],
                           oe["latest_fcf"], r, unit=unit)
    liq_json, liq_md = LiquidationAssessor()(acct, unit=unit)
    alerts = [str(a) for a in liq_json.get("alerts") or []]
    logger.info("估值表格已生成（DCF 矩阵 + 清算 %s，%d 条告警）",
                "适用" if liq_json.get("applicable") else "不适用", len(alerts))
    return {"{{dcf_table}}": dcf_md, "{{liquidation_table}}": liq_md}, alerts


def write_meta(ctx: Ctx, template: Path, started_at: datetime,
               issues: list | None = None, failed: bool = False) -> None:
    """在报告旁写 meta.json，供 Web 端索引展示。issues 非空即异常，空 = 无异常。

    failed=True 表示本次没有产出报告，report_file 置空——否则磁盘上上一次成功运行的
    report.md 会被索引成本次结果，异常清单里又查不到这次失败。
    """
    report = ctx.output
    meta = {
        "market": ctx.market,
        "code": ctx.code,
        "company_name": ctx.company_name,
        "template": template.stem.removeprefix("template_"),
        "model": ctx.lm.model,
        "filings": len(ctx.filings),
        "status": "failed" if failed else "ok",
        "report_file": None if failed else report.name,
        "report_chars": 0 if failed else
        (len(report.read_text(encoding="utf-8")) if report.exists() else 0),
        "issues": issues or [],
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "started_at": started_at.isoformat(timespec="seconds"),
    }
    report.parent.mkdir(parents=True, exist_ok=True)
    report.with_name("meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    logger.info("meta 已写入 %s", report.with_name("meta.json"))


def run_accounting_stage(ctx: Ctx) -> AccountingTable | None:
    acct = AccountingBuilder()(ctx.derived, ctx.filings)
    if acct and acct.years:
        return acct
    logger.warning("数字表提取失败")
    return None


def _fin_and_tables(ctx: Ctx, acct: AccountingTable | None) -> tuple[dict | None, dict[str, str]]:
    """数字表 → 锚表结构 + 程序直渲的财务数据表/盈利质量对账表。"""
    fin = None
    if acct and acct.years:
        fin = build_financials(acct.values, currency=acct.currency or None, table_tail=5)
    else:
        logger.warning("无数字表，财务锚表缺失（报告将不带财务表格）")
        return None, {}
    unit = f"亿{fin['currency']}" if fin.get("currency") else "亿（币种未标注）"
    tables = {
        "{{fin_table}}": f"单位：{unit}。金额以合并报表为准。\n\n" + fin["markdown"],
        "{{earnings_check}}": fin["owner_earnings"]["markdown"],
    }
    return fin, tables


def run_evidence_stage(ctx: Ctx, fin: dict | None,
                       acct: AccountingTable | None) -> tuple[dict | None, list[dict]]:
    """逐章摘要与叙事（lib/qual.py）→ 跨份合并 → 程序装配 meta/narrative。

    返回 (evidence, quals)；quals 供证据合并失败时的报告回退使用。
    """
    quals = build_qual(ctx.lm, ctx.reader, ctx.derived, ctx.filings)
    anchor = anchor_text(fin) if fin else "（财务数字表提取失败）"
    composer = EvidenceComposer()
    evidence = composer(ctx.lm, ctx.code, ctx.market, quals, anchor)
    if not evidence:
        return None, quals
    # meta 由程序装配（公司名/币种/报告期都是已知事实，不劳烦 AI）
    evidence["meta"] = {
        "company_name": ctx.company_name, "code": ctx.code,
        "currency": (fin or {}).get("currency") or (acct.currency if acct else ""),
        "reporting_periods": (fin or {}).get("years") or (acct.years if acct else []),
        "required_return": 0.10,
    }
    if fin:
        evidence["_accounting_md"] = fin["markdown"] + "\n\n" + fin["owner_earnings"]["markdown"]
    narratives = [{"filing": q["filing"], "file": q["file"],
                   "entries": [e for e in q["narrative"].get("entries", [])
                               if isinstance(e, dict)]}
                  for q in quals
                  if isinstance(q.get("narrative"), dict)
                  and q["narrative"].get("entries")]
    if narratives:
        evolution = composer.compose_evolution(ctx.lm, narratives)
        yearly = [e for n in narratives for e in n["entries"]]
        evidence["narrative"] = {"yearly": yearly, "evolution": evolution}
        logger.info("管理层叙事已综合（%d 条逐年叙事）", len(yearly))
    return evidence, quals


def run_report_stage(ctx: Ctx, evidence: dict | None, acct: AccountingTable | None,
                     fin: dict | None, tables: dict[str, str],
                     quals: list[dict] | None, toc_alerts: list[tuple[str, str]]) -> list[dict]:
    """估值表格（程序）→ 渲染 → 占位符替换 → 对账一轮 → 落盘。

    所有异常（恒等式、数字表缺失、章节目录/定性材料缺失、清算清单告警、对账不一致、
    调用失败）记入 issues 随报告交付（披露替代修复）；issues 为空 = 无异常。信息丢失
    只表现为假阴性，所以每一处「某份公告没进材料」都必须留痕，不能只写日志。
    """
    issues: list[dict] = [{"where": "索引", "item": f, "problem": m} for f, m in toc_alerts]
    if acct:
        issues += [{"where": "数字表", "item": "恒等式", "problem": a}
                   for a in acct.identity_alerts]
        issues += [{"where": "数字表", "item": "提取", "problem": a}
                   for a in acct.stage_alerts]
    if fin is None:
        issues.append({"where": "数字表", "item": "整体",
                       "problem": "数字表提取失败，数字表格缺失"})
    for f in ctx.filings:
        missing_pages = page_failures(ctx.derived / f["file"])
        if missing_pages:
            pages = "、".join(str(n) for n in missing_pages)
            issues.append({"where": "OCR", "item": f["file"],
                           "problem": f"第 {pages} 页解析失败，内容缺失"})
    covered = {q["file"] for q in (quals or []) if q.get("summaries")}
    issues += [{"where": "定性", "item": f["file"],
                "problem": "该份公告没有进入定性材料（无章节目录、或选章/摘要为空），"
                           "报告只反映其余公告"}
               for f in ctx.filings if f["file"] not in covered]
    narrative = (evidence or {}).get("narrative")
    if isinstance(narrative, dict) and narrative.get("evolution") is None:
        issues.append({"where": "定性", "item": "叙事演化",
                       "problem": "跨年叙事综合失败，管理层言行记录缺少年际轨迹"})
    writer = ReportWriter()
    if evidence is None:
        logger.warning("回退到直接撰写模式")
        issues.append({"where": "证据", "item": "整体",
                       "problem": "证据合并失败，回退直写模式"})
        if acct and fin:
            valuation, alerts = compute_valuation_tables({}, acct, fin)
            tables = {**tables, **valuation}
            issues += [{"where": "估值", "item": "清算", "problem": a} for a in alerts]
        content = writer.direct_write(ctx.lm, ctx.template_text, ctx.code, ctx.market,
                                      quals or [])
        content = _substitute_tables(content, tables)
        _save_report(content, ctx.output)
        return issues

    content = writer(ctx.lm, ctx.template_text, evidence, ctx.code, ctx.market)
    if acct and fin:
        valuation, alerts = compute_valuation_tables(evidence, acct, fin)
        tables = {**tables, **valuation}
        issues += [{"where": "估值", "item": "清算", "problem": a} for a in alerts]
    content = _substitute_tables(content, tables)
    # 报告中的表格数字出自程序渲染而非证据 JSON，对账时一并提供作基准
    check = FidelityChecker()(ctx.lm, content, {**evidence, "_rendered_tables": tables},
                              ctx.code, ctx.market)
    if check is None:
        issues.append({"where": "保真对账", "item": "整体", "problem": "对账调用失败"})
    else:
        issues.extend(check)
        if check:
            logger.warning("保真对账 %d 处不一致，已写入 meta.json", len(check))
    _save_report(content, ctx.output)
    return issues


def _save_report(content: str, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content + "\n", encoding="utf-8")
    logger.info("报告已写入 %s（%d 字符）", output, len(content))


def main() -> None:
    p = argparse.ArgumentParser(description="财报分析 agent")
    p.add_argument("--market", default="hk")
    p.add_argument("--code", default="02513")
    p.add_argument("--template", type=Path, default=Path("templates/template_business_model_single_company.md"))
    p.add_argument("--output", type=Path, default=None,
                   help="报告输出路径（默认 reports/<market>/<code>/report.md）")
    p.add_argument("--trace", action="store_true",
                   help="记录每次 LLM 请求到 reports/.trace/agent-<时间戳>.jsonl")
    p.add_argument("--full", action="store_true",
                   help="trace 额外写 full/ 完整请求响应（默认只记摘要）")
    args = p.parse_args()

    trace_path = None
    if args.trace:
        trace_path = REPORTS_ROOT / ".trace" / f"agent-{datetime.now():%Y%m%d-%H%M%S}.jsonl"
        logger.info("trace 路径: %s", trace_path)
    lm = try_connect(trace_path=trace_path, trace_full=args.full)
    if lm is None:
        raise SystemExit(f"无法连接 LLM 服务（{LLM_BASE}）")

    output = args.output or REPORTS_ROOT / args.market / args.code / "report.md"
    reader = LakehouseReader()
    ctx = prepare(lm, reader, args.market, args.code, args.template, output)
    total_bytes = sum(f["bytes"] for f in ctx.filings)
    logger.info("模型: %s", lm.model)
    logger.info("公告: %d 份共 %.1fMB, 输出: %s", len(ctx.filings), total_bytes / 1e6, output)

    started_at = datetime.now()
    try:
        acct = run_accounting_stage(ctx)
        fin, tables = _fin_and_tables(ctx, acct)
        toc_alerts = _ensure_tocs(ctx)
        evidence, quals = run_evidence_stage(ctx, fin, acct)
        issues = run_report_stage(ctx, evidence, acct, fin, tables, quals, toc_alerts)
    except Exception as e:
        # 崩了也要留痕：不写 meta，上一次成功运行的 report.md 会被当成本次结果，
        # 而异常清单里永远不会有这次失败。
        logger.exception("报告生成失败")
        if args.output is None:
            write_meta(ctx, args.template, started_at, failed=True,
                       issues=[{"where": "运行", "item": "整体",
                                "problem": f"{type(e).__name__}: {e}"}])
        raise SystemExit(1) from e
    if args.output is None:  # meta.json 只登记默认路径的主报告，避免对比/自定义输出覆盖
        write_meta(ctx, args.template, started_at, issues=issues)


if __name__ == "__main__":
    main()
