import json
import logging
import textwrap
from datetime import datetime
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

DEFAULT_MAX_TOKENS = 2048  # 所有生成都设上限；调用方可按需抬高
TRACE_DIR = Path("output/trace")


class Agent:
    def __init__(self, base_url: str = "http://localhost:9931/v1", model: str = "", trace: bool = False):
        self.base_url = base_url
        self.model = model
        self.trace = trace
        self.session = f"agent-{datetime.now().strftime('%Y%m%d%H%M%S')}"

    # —— 服务信息 ——
    def tokenize(self, text: str) -> list[int]:
        """llama.cpp 的 /tokenize：返回 token id 列表。"""
        response = requests.post(f"{self.base_url.removesuffix('/v1')}/tokenize", json={"content": text})
        response.raise_for_status()
        return response.json()["tokens"]

    def n_ctx(self) -> int:
        """服务端上下文长度（/props），缓存；调用方按它决定 prefill 预算。"""
        if not hasattr(self, "_n_ctx"):
            response = requests.get(f"{self.base_url.removesuffix('/v1')}/props")
            response.raise_for_status()
            self._n_ctx: int = response.json()["default_generation_settings"]["n_ctx"]
        return self._n_ctx

    def chat(self, prompt: str, schema: dict | None = None, max_tokens: int | None = None, temperature: float = 0) -> str:
        """一次生成。所有调用都走这里，前缀复用、上限、记账都在这一层。"""
        data = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens or DEFAULT_MAX_TOKENS,
            "temperature": temperature,
        }
        if schema is not None:
            data["response_format"] = {"type": "json_schema", "json_schema": {"name": "response", "schema": schema}}

        response = requests.post(f"{self.base_url}/chat/completions", headers={"Content-Type": "application/json"}, json=data)
        if response.status_code >= 400:
            raise RuntimeError(f"模型服务返回 {response.status_code}：{response.text[:400]}（prompt {len(prompt)} 字符，max_tokens {data['max_tokens']}）")

        response.raise_for_status()
        body = response.json()
        choice = body["choices"][0]
        content = choice["message"]["content"]

        if self.trace:
            self._trace(data, content)
        return content

    def chat_json(self, prompt: str, schema: dict, max_tokens: int | None = None) -> dict:
        return json.loads(self.chat(prompt, schema=schema, max_tokens=max_tokens))

    def fits_budget(self, text: str, reserve: int = 8192) -> bool:
        """按上下文预算判断：文本装得下直读（再留 reserve 给输出）。"""
        return len(self.tokenize(text)) < self.n_ctx() - reserve

    def chat_distill(self, prompt: str, chunks: list[str]) -> str:
        """按上下文预算选路线：装得下直读（避免逐块归并丢细节），装不下退回逐块归并。
        chunk（年报原文）比 prompt 更不可变、多个任务跑同一份，所以顶在最前：
        前缀匹配能跨任务、跨阶段整段复用，不要把 prompt 挪到 chunk 前面。"""
        text = "\n".join(chunks)
        if self.fits_budget(text):
            return self.chat(f"<chunk>\n{text}\n</chunk>\n\n{prompt}").strip()
        return self.chat_reduce_chunks(prompt, chunks, loops=2).strip()

    def chat_reduce_chunks(self, prompt: str, chunks: list[str], loops: int = 2) -> str:
        """逐块归并：每块只看当前结果与新块，输出要求尽量短。
        按不可变程度排序组织 prompt：reduce-rule 逐字不变 > chunk 跨圈跨任务复现 >
        prompt 任务内不变 > current-result 每次在变，前缀从长到短匹配，KV 复用交给引擎；
        一整圈没有任何块需要更新时提前结束，不空跑下一圈。"""
        rule = textwrap.dedent("""
            <reduce-rule>
                目前已经整理好的结果在 <current-result>，把 <chunk> 里的信息合并进来并重新整理，不要重复，删除冲突部分；
                需要更新结果时 updated=true，返回新的result；
                结果不需要更新时 updated=false, result留空
            </reduce-rule>
        """).strip()
        result = ""
        for _ in range(loops):
            changed = False
            for chunk in chunks:
                message = f"{rule}\n\n<chunk>\n{chunk}\n</chunk>\n\n{prompt}\n\n<current-result>\n{result}\n</current-result>"
                resp = self.chat_json(
                    message, {"type": "object", "properties": {"updated": {"type": "boolean"}, "result": {"type": "string"}}, "required": ["updated", "result"]}
                )
                if resp.pop("updated", True) and resp.get("result", "").strip():
                    result = resp["result"]
                    changed = True
            if not changed:
                break
        return result

    def _trace(self, request: dict, content: str) -> None:
        TRACE_DIR.mkdir(parents=True, exist_ok=True)
        record = {
            "request": request,
            "response": content,
        }
        with open(TRACE_DIR / f"{self.session}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
