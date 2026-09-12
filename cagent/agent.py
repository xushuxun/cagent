import json
from datetime import datetime

import requests


class Agent:
    def __init__(self, base_url: str = "http://localhost:9931/v1", model: str = "", trace: bool = False):
        self.base_url = base_url
        self.model = model
        self.trace = trace
        self.agent_id = f"agent-{datetime.now().strftime('%Y%m%d%H%M%S')}"

    def chat(self, messages: list[dict], response_format: dict | None = None) -> str:
        """对应 openai sdk 的 client.chat.completions.create，入参 messages，返回 content 字符串。"""
        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        data = {
            "model": self.model,
            "messages": messages,
            "max_tokens": 65536,
            "temperature": 0.7,
            "top_p": 0.80,
            "top_k": 20,
            "min_p": 0.0,
            "presence_penalty": 1.5,
            "repetition_penalty": 1.0,
        }
        if response_format is not None:
            data["response_format"] = response_format
        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        if self.trace:
            with open(f"{self.agent_id}.jsonl", "a", encoding="utf-8") as f:
                record = {"request": data, "response": content}
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return content