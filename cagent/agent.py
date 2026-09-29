import json
from datetime import datetime
from pathlib import Path

import requests


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
            # "temperature": 0.7,
            # "top_p": 0.80,
            # "top_k": 20,
            # "min_p": 0.0,
            # "presence_penalty": 1.5,
            # "repetition_penalty": 1.0,
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
