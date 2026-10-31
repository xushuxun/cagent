"""最小 Recursive Language Model：模型通过持久 Python REPL 查看外部上下文并递归查询。"""

import json
import re
from contextlib import redirect_stdout
from decimal import Decimal
from io import StringIO
from typing import Any

from cagent.core.agent import Agent

_CODE_RE = re.compile(r"```(?:python)?\s*\n(.*?)```", re.DOTALL)
_FINAL_RE = re.compile(r"FINAL_VAR\(\s*([A-Za-z_]\w*)\s*\)")


class RLM:
    """一层递归的 RLM；上下文只存在于 REPL，不直接塞给根模型。"""

    def __init__(self, agent: Agent, max_iterations: int = 12):
        self.agent = agent
        self.max_iterations = max_iterations

    def completion(self, context: Any, query: str) -> str:
        env: dict[str, Any] = {"context": context, "Decimal": Decimal, "json": json, "llm_query": self._llm_query}

        def final_var(value: Any) -> None:
            env["_result"] = env[value] if isinstance(value, str) and value in env else value

        env["FINAL_VAR"] = final_var
        history = ""
        for iteration in range(self.max_iterations):
            prompt = f"""你在一个持久 Python REPL 中工作。输入不在对话里，而在变量 context 中；不要假设你已经看过它。
可用变量：context、Decimal、json；可用函数：print(...)、llm_query(prompt)（对局部内容做一次子模型语义理解，不能递归）。
每轮只输出一个简洁的 python 代码块来查看、分解或计算 context；执行结果会在下一轮返回。不要输出解释。
用循环与辅助函数复用逻辑，不要逐项复制代码。完成任务后，把结果赋给变量，并只输出 FINAL_VAR(变量名)。

<task>
{query}
</task>

<context>
类型：{type(context).__name__}；JSON 字符数：{len(json.dumps(context, ensure_ascii=False))}
</context>

<history>
{history}
</history>"""
            response = self.agent.chat(prompt, max_tokens=4096).strip()
            final = _FINAL_RE.fullmatch(response)
            if final:
                name = final.group(1)
                if name not in env:
                    raise ValueError(f"RLM 返回了不存在的变量: {name}")
                value = env[name]
                return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            match = _CODE_RE.fullmatch(response)
            if not match:
                history += f"\n第 {iteration + 1} 轮无效回复：\n{response}\n请只输出代码块或 FINAL_VAR(变量名)。\n"
                continue
            code = match.group(1)
            stdout = StringIO()
            try:
                with redirect_stdout(stdout):
                    exec(code, env)  # noqa: S102 模型生成的代码是 RLM 的核心机制
                if "_result" in env:
                    value = env["_result"]
                    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
                if not callable(env["FINAL_VAR"]):
                    value = env["FINAL_VAR"]
                    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
                result = stdout.getvalue() or "（执行成功，无输出）"
            except Exception as exc:
                result = f"{stdout.getvalue()}\n{type(exc).__name__}: {exc}"
            history += f"\n第 {iteration + 1} 轮代码：\n```python\n{code}\n```\n执行结果：\n{result[:12000]}\n"
        raise RuntimeError(f"RLM 在 {self.max_iterations} 轮内未返回结果")

    def _llm_query(self, prompt: str) -> str:
        return self.agent.chat(prompt, max_tokens=4096)
