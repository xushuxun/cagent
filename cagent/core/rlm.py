"""最小 RLM：模型在持久 Python REPL 中查看 context、生成并执行代码。"""

import json
import re
from contextlib import redirect_stdout
from decimal import Decimal
from io import StringIO
from typing import Any

from cagent.core.agent import Agent

_CODE = re.compile(r"```(?:python)?\s*\n(.*?)```", re.DOTALL)
_FINAL = re.compile(r"FINAL_VAR\(\s*([A-Za-z_]\w*)\s*\)")


class RLM:
    def __init__(self, agent: Agent, max_iterations: int = 6):
        self.agent = agent
        self.max_iterations = max_iterations

    def completion(self, context: Any, query: str) -> str:
        env: dict[str, Any] = {"context": context, "Decimal": Decimal, "json": json}

        def final_var(value: Any) -> None:
            env["_result"] = env[value] if isinstance(value, str) and value in env else value

        env["FINAL_VAR"] = final_var
        preview = json.dumps(context, ensure_ascii=False)[:2000]
        history = ""
        for _ in range(self.max_iterations):
            prompt = f"""你在持久 Python REPL 中工作，输入在 context 中。
先根据预览和实际执行结果确认数据结构，再写代码；不要假设字段。
每轮只输出一个简洁 Python 代码块；可用 Decimal、json 和 FINAL_VAR(变量或变量名)。
执行结果会反馈给你。完成后只输出 FINAL_VAR(结果变量名)。

<context-preview>{preview}</context-preview>
<task>{query}</task>
<history>{history}</history>"""
            response = self.agent.chat(prompt, max_tokens=4096).strip()
            final = _FINAL.fullmatch(response)
            if final:
                value = env[final.group(1)]
                return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            match = _CODE.fullmatch(response)
            if not match:
                history += f"\n无效回复：{response}\n"
                continue
            code = match.group(1)
            output = StringIO()
            try:
                with redirect_stdout(output):
                    exec(code, env)  # noqa: S102 RLM 的核心就是执行模型生成的代码
                if "_result" in env:
                    value = env["_result"]
                    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
                if not callable(env["FINAL_VAR"]):
                    value = env["FINAL_VAR"]
                    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
                result = output.getvalue() or "执行成功"
            except Exception as exc:
                result = f"{type(exc).__name__}: {exc}"
            history += f"\n代码：\n{code}\n结果：\n{result[:8000]}\n"
        raise RuntimeError("RLM 未在限定轮次内返回结果")
