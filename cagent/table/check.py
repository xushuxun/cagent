"""RLM 只对固定合计目标尝试配平，失败仅告警，不修改提取值。"""

import json

from cagent.core.agent import Agent
from cagent.core.rlm import RLM

_TARGETS = {
    "bs": ["资产总计", "负债合计", "股东权益合计", "负债和股东权益总计"],
    "is": ["营业利润", "利润总额", "净利润", "综合收益总额"],
    "cf": ["各类活动现金流量净额", "现金及现金等价物净增减额", "年末现金及现金等价物余额"],
}


def check_statement(agent: Agent, rows: list[dict], kind: str = "bs") -> list[dict]:
    query = f"""对 context.rows 中的财务报表做配平检查。
只检查这些目标：{json.dumps(_TARGETS[kind], ensure_ascii=False)}。
自行对齐不同公司的实际 item 名称，选择候选加项和减项，用 Decimal 对带符号 value 求和；不要预设组成项。
返回 checks 列表，每项包含 target、item、add、sub、computed、reported、ok；item 必须是对应报表中的实际科目名，add/sub 用 item 名称。
不属于当前表的目标直接忽略；目标存在但无法配平时 ok=false，computed 可为空字符串。不要修改 rows，不要为配平编造数值。
最后输出 FINAL_VAR(checks)。"""
    context = {"rows": [{"item": row["item"], "value": row["value"]} for row in rows if row["value"] is not None]}
    checks = json.loads(RLM(agent, max_iterations=10).completion(context, query))
    if not isinstance(checks, list):
        raise TypeError("RLM 配平结果不是列表")
    return [{**check, "item": check["item"] or check["target"]} for check in checks if check["reported"] or check["computed"]]
