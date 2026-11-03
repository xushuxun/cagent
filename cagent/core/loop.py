"""极简 agent loop：模型调用工具，程序执行并回写结果，直到最终回答。"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from cagent.core.agent import Agent

ToolHandler = Callable[..., Any]
EventHandler = Callable[[dict], None]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    handler: ToolHandler
    schema: dict


class AgentLoop:
    def __init__(self, agent: Agent, tools: list[Tool], on_event: EventHandler | None = None):
        self.agent = agent
        self.tools = {tool.name: tool for tool in tools}
        self.on_event = on_event
        self.messages: list[dict] = []

    def emit(self, kind: str, **payload: Any) -> None:
        event = {"type": kind, **payload}
        trace_event = getattr(self.agent, "trace_event", None)
        if trace_event:
            trace_event(event)
        if self.on_event:
            self.on_event(event)

    def run(self, prompt: str, max_turns: int = 8) -> str:
        self.messages.append({"role": "user", "content": prompt})
        self.emit("loop_start", prompt=prompt)
        for turn in range(1, max_turns + 1):
            self.emit("turn_start", turn=turn)
            response = self._next()
            self.messages.append({"role": "assistant", **response})
            self.emit("assistant", response=response)
            calls = response.get("tool_calls", [])
            if not calls:
                self.emit("turn_end", turn=turn)
                return response.get("content", "")
            for call in calls:
                self._call_tool(call)
        raise RuntimeError(f"agent 超过 {max_turns} 轮仍未完成")

    def _next(self) -> dict:
        tools = [{"name": tool.name, "description": tool.description, "schema": tool.schema} for tool in self.tools.values()]
        schema = {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "tool_calls": {"type": "array", "items": {"type": "object", "properties": {"name": {"type": "string"}, "arguments": {"type": "object"}}, "required": ["name", "arguments"]}},
            },
            "required": ["content", "tool_calls"],
        }
        prompt = f"""继续完成任务。需要工具时填写 tool_calls；完成时 tool_calls=[] 且 content 是最终回答。
只调用已列出的工具，参数必须符合 schema。
<tools>{tools}</tools>
<messages>{self.messages}</messages>"""
        for attempt in range(2):
            try:
                return self.agent.chat_json(prompt, schema, max_tokens=16384)
            except RuntimeError as error:
                if "structured_output_failed" not in str(error) or attempt == 1:
                    raise
                self.emit("structured_output_retry", error=str(error))
        raise AssertionError

    def _call_tool(self, call: dict) -> None:
        name, arguments = call["name"], call["arguments"]
        if name not in self.tools:
            raise ValueError(f"未知工具: {name}")
        self.emit("tool_call", name=name, arguments=arguments)
        try:
            result = self.tools[name].handler(**arguments)
        except Exception as error:
            result = {"error": str(error)}
        self.messages.append({"role": "tool", "name": name, "content": result})
        self.emit("tool_result", name=name, result=result)
