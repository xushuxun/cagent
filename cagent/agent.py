import json
import re
import textwrap
from datetime import datetime
from pathlib import Path

import requests


def chunk_text(text: str, budget: int = 32768) -> list[str]:
    """按字符预算切文本（约 2 字符/token，budget 取 n_ctx 即约 n_ctx/2 token）。
    切口优先落在 md 标题（行首 #）处，避免截断段落；标题间距超预算时退回硬切。"""
    heading_positions = [match.start() for match in re.finditer(r"(?m)^#", text)]
    chunks, chunk_start, previous_heading = [], 0, 0
    for position in heading_positions + [len(text)]:
        if position - chunk_start > budget:
            cut_position = previous_heading if previous_heading > chunk_start else position
            chunks.append(text[chunk_start:cut_position])
            chunk_start = cut_position
        previous_heading = position
    chunks.append(text[chunk_start:])
    return chunks


class Agent:
    def __init__(
        self,
        base_url: str = "http://localhost:9931/v1",
        model: str = "",
        trace: bool = False,
    ):
        self.base_url = base_url
        self.model = model
        self.trace = trace
        self.agent_id = f"agent-{datetime.now().strftime('%Y%m%d%H%M%S')}"

    def tokenize(self, text: str) -> list[int]:
        """对应 llama.cpp server 的 /tokenize 端点，返回 token id 列表。"""
        response = requests.post(f"{self.base_url.removesuffix('/v1')}/tokenize", json={"content": text})
        response.raise_for_status()
        return response.json()["tokens"]

    def n_ctx(self) -> int:
        """服务端上下文长度（llama.cpp /props），结果缓存。"""
        if not hasattr(self, "_n_ctx"):
            response = requests.get(f"{self.base_url.removesuffix('/v1')}/props")
            response.raise_for_status()
            self._n_ctx: int = response.json()["default_generation_settings"]["n_ctx"]
        return self._n_ctx

    def chat(self, messages: list[dict], **params) -> str:
        """对应 openai sdk 的 client.chat.completions.create，入参 messages，返回 content 字符串。"""
        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        data = {
            "model": self.model,
            "messages": messages,
            "max_tokens": 65536,
            "temperature": 0,
        }
        data.update(params)
        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        if self.trace:
            Path("output/trace").mkdir(parents=True, exist_ok=True)
            with open(f"output/trace/{self.agent_id}.jsonl", "a", encoding="utf-8") as f:
                record = {"request": data, "response": content}
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return content

    def chat_json(self, prompt: str, schema: dict) -> dict:
        """带 json_schema 约束的聊天，temperature=0，返回解析后的 dict。"""
        return json.loads(
            self.chat(
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_schema", "json_schema": {"name": "response", "schema": schema}},
                temperature=0,
            )
        )

    def pick_chapters(self, chapters: list[dict], task: str) -> list[int]:
        """给精确的章节标题列表和阅读目的，让模型选出要精读的章节序号。

        不做标题关键字匹配——选章是理解任务，交给模型，每家公司每年选一次。
        """
        listing = "\n".join(f'<chapter index="{index}">\n{chapter["title"]}\n</chapter>' for index, chapter in enumerate(chapters))
        prompt = textwrap.dedent(f"""
            <requirements>
            {task}
            从列表里选，返回章节序号。
            </requirements>

            <chapters>
            {listing}
            </chapters>
        """).strip()
        indexes = self.chat_json(
            prompt,
            {
                "type": "object",
                "properties": {"indexes": {"type": "array", "items": {"type": "integer"}}},
                "required": ["indexes"],
            },
        )["indexes"]
        picked = [index for index in indexes if 0 <= index < len(chapters)]
        if not picked:
            raise ValueError("模型没有返回有效章节序号")
        return picked
