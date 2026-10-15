"""llm：模型调用层

统一入口，三条纪律写在这里，不靠调用方自觉：

    共享前缀   一个任务的所有调用共用同一段前缀（一字不改），变化的部分只放在尾巴里，
               服务端就能复用 KV——prefill 比 decode 快一个数量级，省的是 decode。
    输出上限   每次生成都有 max_tokens；打满上限会告警，别让它悄悄截断。
    失败收敛   校验驱动的多轮迭代：把失败原因反馈回去重试，只在结果变好时采纳，收敛不了就停手。

每次调用都记账（调用次数、prompt/response 字符、耗时、是否截断），既写进 trace 也留在内存里，
阶段记录与性能优化都从这里取数。

用法：
    from cagent.llm import LLM

    llm = LLM(trace=True)
    task = llm.task(prefix)                      # 前缀固定，尾巴随任务变
    text = task.call("只写这一维：…", max_tokens=512)
    data = task.call("按四元数组输出", schema={...})

    # 校验驱动的收敛：parse 出结构、check 给分与反馈，分数变低才采纳
    result, score, feedback = task.converge(tail, parse=…, check=…, attempts=2)
"""

import json
import logging
import textwrap
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

DEFAULT_MAX_TOKENS = 2048     # 所有生成都设上限；调用方可按需抬高
TRACE_DIR = Path("output/trace")


@dataclass
class Usage:
    """一次调用的账。"""

    prompt_chars: int
    response_chars: int
    seconds: float
    max_tokens: int
    truncated: bool
    schema: bool


@dataclass
class Ledger:
    """一次运行的总账：阶段记录直接取这里的数。"""

    calls: list[Usage] = field(default_factory=list)

    @property
    def prompt_tokens(self) -> float:
        return sum(u.prompt_chars for u in self.calls) / 1.6   # 中文粗估：1 token ≈ 1.6 字符

    @property
    def response_tokens(self) -> float:
        return sum(u.response_chars for u in self.calls) / 1.6

    @property
    def seconds(self) -> float:
        return sum(u.seconds for u in self.calls)

    def summary(self) -> str:
        truncated = sum(1 for u in self.calls if u.truncated)
        return (
            f"{len(self.calls)} 次调用 | prefill ≈ {self.prompt_tokens:,.0f} tokens | "
            f"decode ≈ {self.response_tokens:,.0f} tokens | 服务端耗时 {self.seconds:,.0f}s"
            + (f" | 截断 {truncated} 次" if truncated else "")
        )


class LLM:
    def __init__(self, base_url: str = "http://localhost:9931/v1", model: str = "", trace: bool = False):
        self.base_url = base_url
        self.model = model
        self.trace = trace
        self.session = f"agent-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        self.ledger = Ledger()

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

    # —— 唯一的线上路径 ——
    def complete(self, prompt: str, *, schema: dict | None = None, max_tokens: int | None = None, temperature: float = 0) -> str:
        """一次生成。所有调用都走这里，前缀复用、上限、记账都在这一层。"""
        data: dict[str, Any] = {"model": self.model, "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens or DEFAULT_MAX_TOKENS, "temperature": temperature}
        if schema is not None:
            data["response_format"] = {"type": "json_schema", "json_schema": {"name": "response", "schema": schema}}
        started = time.time()
        response = requests.post(f"{self.base_url}/chat/completions", headers={"Content-Type": "application/json"}, json=data)
        if response.status_code >= 400:
            # 服务端会说明是上下文超了还是语法编译失败，带出来才不用猜
            raise RuntimeError(f"模型服务返回 {response.status_code}：{response.text[:400]}（prompt {len(prompt)} 字符，max_tokens {data['max_tokens']}）")
        response.raise_for_status()
        body = response.json()
        choice = body["choices"][0]
        content = choice["message"]["content"]
        usage = Usage(
            prompt_chars=len(prompt),
            response_chars=len(content),
            seconds=time.time() - started,
            max_tokens=data["max_tokens"],
            truncated=choice.get("finish_reason") == "length",
            schema=schema is not None,
        )
        self.ledger.calls.append(usage)
        if usage.truncated:
            logger.warning(f"生成打满 max_tokens={usage.max_tokens}，可能被截断（prompt {usage.prompt_chars} 字符）")
        if self.trace:
            self._trace(prompt, content, usage)
        return content

    def json(self, prompt: str, schema: dict, *, max_tokens: int | None = None) -> dict:
        return json.loads(self.complete(prompt, schema=schema, max_tokens=max_tokens))

    # —— 任务：固定前缀 + 变化尾巴 ——
    def task(self, prefix: str) -> Task:
        return Task(self, prefix)

    def distill(self, prompt: str, chunks: list[str]) -> str:
        """按上下文预算选路线：装得下直读（避免逐块归并丢细节），装不下退回逐块归并。"""
        text = "\n".join(chunks)
        if len(self.tokenize(text)) < self.n_ctx() - 16384:
            return self.complete(f"{prompt}\n\n<report>\n{text}\n</report>").strip()
        return self.reduce_chunks(prompt, chunks, loops=2)

    def reduce_chunks(self, prompt: str, chunks: list[str], *, loops: int = 1) -> str:
        """逐块归并：每块只看当前结果与新块，输出要求尽量短。"""
        result = ""
        for _ in range(loops):
            for chunk in chunks:
                message = textwrap.dedent(f"""
                    {prompt}

                    <reduce-rule>
                        目前已经整理好的结果在 <current-result>，把 <chunk> 里的信息合并进来并重新整理，不要重复，删除冲突部分；
                        需要更新结果时 updated=true，返回新的result；
                        结果不需要更新时 updated=false, result留空
                    </reduce-rule>

                    <current-result>
                        {result}
                    </current-result>

                    <chunk>
                        {chunk}
                    </chunk>""").strip()
                resp = self.json(message, {"type": "object", "properties": {"updated": {"type": "boolean"}, "result": {"type": "string"}}, "required": ["updated", "result"]})
                if resp.pop("updated", True) and resp.get("result", "").strip():
                    result = resp["result"]
        return result.strip()

    # —— 过渡 API：其余模块还在用，随 S6/S7 逐批迁到 task().call() ——
    def chat_prompt(self, prompt: str) -> str:
        return self.complete(prompt)

    def chat_json(self, prompt: str, schema: dict) -> dict:
        return self.json(prompt, schema)

    def chat_distill(self, prompt: str, chunks: list[str]) -> str:
        return self.distill(prompt, chunks)

    def chat_reduce_chunks(self, prompt: str, chunks: list[str], result: str = "") -> str:
        if result:
            logger.debug("reduce 的起始结果由调用方给出，本层只做逐块归并")
        return self.reduce_chunks(prompt, chunks)

    def chat_reduce_chunks_loop_once(self, prompt: str, chunks: list[str]) -> str:
        return self.reduce_chunks(prompt, chunks, loops=2)

    def _trace(self, prompt: str, content: str, usage: Usage) -> None:
        TRACE_DIR.mkdir(parents=True, exist_ok=True)
        record = {
            "request": {"messages": [{"role": "user", "content": prompt}], "max_tokens": usage.max_tokens},
            "response": content,
            "usage": {"prompt_chars": usage.prompt_chars, "response_chars": usage.response_chars, "seconds": round(usage.seconds, 2), "truncated": usage.truncated},
        }
        with open(TRACE_DIR / f"{self.session}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


class Task:
    """一个任务：固定前缀 + 变化尾巴。前缀一字不改，服务端 KV 才能复用。"""

    def __init__(self, llm: LLM, prefix: str):
        self.llm = llm
        self.prefix = prefix

    def call(self, tail: str, *, schema: dict | None = None, max_tokens: int | None = None) -> str:
        return self.llm.complete(f"{self.prefix}\n\n{tail}".strip(), schema=schema, max_tokens=max_tokens)

    def converge(
        self,
        tail: Callable[[Any | None, str], str],
        *,
        parse: Callable[[str, Any | None], Any],
        check: Callable[[Any], tuple[int, str]],
        schema: dict | Callable[[int, Any | None], dict | None] | None = None,
        max_tokens: int | None = None,
        attempts: int = 2,
    ) -> tuple[Any, int, str]:
        """校验驱动的多轮收敛。

        tail 与 parse 都拿到上一轮结果（第一轮为 None）：重取只回错行，parse 负责把它们并回原表；
        check 给 (分数, 反馈)，分数越低越好。只在分数变低时采纳新结果；分数到 0 或轮次用尽即停手，
        收敛不了就如实返回当前的分数与反馈。schema 可按轮次不同（首轮整表、重取只回错行）。
        """
        best: Any = None
        best_score: int | None = None
        feedback = ""
        for round_no in range(1, attempts + 1):
            round_schema = schema(round_no, best) if callable(schema) else schema
            assert round_schema is None or isinstance(round_schema, dict)
            raw = self.call(tail(best, feedback), schema=round_schema, max_tokens=max_tokens)
            try:
                value = parse(raw, best)
            except (ValueError, KeyError, TypeError) as exc:
                feedback = f"上一轮返回无法解析：{exc}。请严格按要求的格式重给。"
                logger.warning(f"第 {round_no} 轮解析失败：{exc}")
                continue
            score, feedback = check(value)
            logger.info(f"第 {round_no} 轮：分数 {score}{'（已收敛）' if score == 0 else ''}")
            if best_score is None or score < best_score:
                best, best_score = value, score
            if score == 0:
                break
        return best, (best_score if best_score is not None else -1), feedback
