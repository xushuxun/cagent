"""报告保真检测：独立上下文对账报告数字 vs 证据 JSON（只检测不改写，
检出的不一致由调用方记入 issues 随报告交付）；think 泄漏检查。"""

import json
import logging
import re

import dspy

from cagent.llm import DETERMINISTIC, parse_json, tag

log = logging.getLogger(__name__)

# 报告/JSON 里表示"无数据"的写法（两端都缺席不算矛盾）
_ABSENT = {"", "null", "none", "未披露", "无", "—", "-"}


_NUM_TOKEN_RE = re.compile(r"-?\d[\d,]*\.?\d*%?")


def _numbers(s: object) -> list[float]:
    """抽出文本里的全部数字（% 折成小数）："38.06亿元"→[38.06]，"5.53%"→[0.0553]。"""
    if isinstance(s, (int, float)):
        return [float(s)]
    if not isinstance(s, str):
        return []
    out: list[float] = []
    for m in _NUM_TOKEN_RE.finditer(s):
        t = m.group(0).replace(",", "")
        pct = t.endswith("%")
        if pct:
            t = t[:-1]
        try:
            v = float(t)
        except ValueError:
            continue
        out.append(v / 100 if pct else v)
    return out


def _is_consistent(issue: dict) -> bool:
    """数值等价（含 %↔小数、舍入、单位后缀、数字列表）或两端都缺席的"问题"是一致项，应过滤。"""
    rv, ev = str(issue.get("report_value") or ""), str(issue.get("evidence_value") or "")
    if rv.strip().lower() in _ABSENT and ev.strip().lower() in _ABSENT:
        return True
    norm = lambda s: re.sub(r"[\s,，。]", "", s)
    if norm(rv) == norm(ev):
        return True
    a, b = _numbers(rv), _numbers(ev)
    # 年份（1900-2100 的 4 位数）不参与数值一致性判断
    a = [v for v in a if not (1900 <= v <= 2100 and v == int(v))]
    b = [v for v in b if not (1900 <= v <= 2100 and v == int(v))]
    if not a or not b:
        return False
    # 每个数字都要在另一端找到容差内的匹配（短列表是长列表的子集也算一致）
    def covered(x: list[float], y: list[float]) -> bool:
        return all(any(abs(p - q) <= max(0.005, abs(q) * 0.01) for q in y) for p in x)
    return covered(a, b) and covered(b, a)


class FidelityCheck(dspy.Signature):
    """逐项核对报告数字（金额/比率/年份序列/估值结果）是否与证据 JSON 一致。
只报矛盾项：数值偏差 >1%、符号相反、单位/币种不符、JSON 为 null 而报告填了具体数字、
JSON 有值而报告必填表（财务数据表与价值估算表）缺失。以下视为一致，一律不列出：
百分数与小数等价（5.53% = 0.0553）、小数位/舍入差异、同一数字的不同单位写法。
叙述性结论冲突（如 JSON 说"收窄"报告说"扩大"）也列出。只输出问题清单，不修改内容；
每个 problem 一句话（≤30 字），禁止引述长句、禁止写核对过程。
JSON 中 _rendered_tables 是程序渲染的数字表格原文，报告里的表格数字以此为准。"""

    payload: str = dspy.InputField(desc="证据 JSON + 报告 Markdown")
    result: str = dspy.OutputField(
        desc='JSON {"issues": [{"where", "item", "report_value", "evidence_value", "problem"}]}'
             '，无矛盾则 {"issues": []}')


class FidelityChecker(dspy.Module):
    """独立上下文对账（不用渲染模型自省）。返回 issues 列表（空 = 无矛盾）；
    调用失败返回 None（检测失败 ≠ 通过，由调用方记降级）。"""

    def __init__(self):
        super().__init__()
        self.check = dspy.Predict(FidelityCheck)

    def forward(self, lm, report_text: str, evidence: dict,
                code: str, market: str) -> list[dict] | None:
        with tag("verify:fidelity"):
            pred = self.check(
                payload=(
                    f"股票 {code}（{market}）的报告与证据 JSON 如下，请核对。\n\n"
                    f"===== 结构化证据 JSON =====\n{json.dumps(evidence, ensure_ascii=False, indent=2)}\n\n"
                    f"===== 报告 Markdown =====\n{report_text}"
                ), lm=lm, config=DETERMINISTIC | {"max_tokens": 8000})
        reply = parse_json(pred.result)
        if not isinstance(reply, dict):
            log.warning("保真对账调用失败（见 trace）")
            return None
        issues = reply.get("issues")
        if not isinstance(issues, list):
            return None
        raw = [it for it in issues if isinstance(it, dict)]
        out = [it for it in raw if not _is_consistent(it)]
        if len(raw) != len(out):
            log.info("保真对账过滤 %d 条数值等价/两端缺席的一致项", len(raw) - len(out))
        if out:
            log.warning("保真对账检出 %d 处不一致: %s",
                        len(out), [str(i.get("item")) for i in out][:10])
        return out
