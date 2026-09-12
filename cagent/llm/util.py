"""dspy 接入层的小工具。"""

import json
import re


def parse_json(content: str) -> dict | list | None:
    """从 LLM 输出中提取首个 JSON 对象/数组，失败返回 None。

    strict=False 兜底：模型有时在 JSON 字符串里写原始换行/控制符。
    """
    m = re.search(r"\{.*\}|\[.*\]", content, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        try:
            return json.loads(m.group(0), strict=False)
        except json.JSONDecodeError:
            return None