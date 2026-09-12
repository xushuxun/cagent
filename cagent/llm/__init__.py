"""cagent 的 dspy 接入层：本地 LLM 客户端、trace 记录、确定性解码约定。

用法：
    lm = connect(trace_path=Path("reports/.trace/xxx.jsonl"))  # 并 dspy.configure
    with tag("环节:步骤"):
        pred = dspy.Predict(MySignature)(...)

本包是 cagent 对 dspy / 本地 llama-server 的唯一接入点；结果进数字表格或被机械
核对的调用一律用 `config=DETERMINISTIC | {"max_tokens": N}`（见 AGENTS.md）。
"""

from cagent.llm.client import (
    DETERMINISTIC,
    LLM_BASE,
    TracedLM,
    connect,
    try_connect,
)
from cagent.llm.trace import TraceSink, current_tag, tag
from cagent.llm.util import parse_json

__all__ = [
    "DETERMINISTIC",
    "LLM_BASE",
    "TraceSink",
    "TracedLM",
    "connect",
    "current_tag",
    "parse_json",
    "tag",
    "try_connect",
]