"""本地 LLM 客户端：TracedLM(dspy.LM) 连 llama-server（OpenAI 兼容）+ 建连。"""

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path

import dspy
import requests

from cagent.llm.trace import TraceSink, _summarize_messages, current_tag

LLM_BASE = "http://127.0.0.1:9931/v1"

# 结果进数字表格或被机械核对的调用一律关掉采样（实测同一份年报在 0.3 下清算价值
# 三次采样极差百亿级）；叙述类调用不在此列。调用处写
# config=DETERMINISTIC | {"max_tokens": N}。
DETERMINISTIC = {"temperature": 0.0}

log = logging.getLogger(__name__)


def _bypass_env_proxy() -> None:
    """本地服务不走环境代理——否则表现为「连不上」，而服务其实是好的。

    两层破坏：requests 把回环请求发给代理拿 502；litellm/httpx 更早在构造 client 时
    就因 socks:// 这类它不认的 scheme 抛 ValueError，no_proxy 也救不了（代理映射在建
    client 时就展开）。只摘掉不认的 scheme，其余条目追加而非覆盖——同进程内的下载环节
    还要出网。
    """
    for key in ("ALL_PROXY", "all_proxy"):
        os.environ.pop(key, None)
    for key in ("no_proxy", "NO_PROXY"):
        old = os.environ.get(key, "")
        os.environ[key] = "127.0.0.1,localhost" + (f",{old}" if old else "")


_RESPONSE_HEAD_CHARS = 500
_RESPONSE_TAIL_CHARS = 200


class TracedLM(dspy.LM):
    """dspy.LM + trace 记录。瞬时故障由 dspy num_retries（指数退避）负责。"""

    def __init__(self, *args, trace_path: Path | None = None,
                 trace_full: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self.trace: TraceSink | None = TraceSink(trace_path) if trace_path else None
        self.trace_full = trace_full

    def forward(self, prompt=None, messages=None, **kwargs):
        t0 = time.monotonic()
        tag_ = current_tag()
        try:
            resp = super().forward(prompt=prompt, messages=messages, **kwargs)
        except Exception as e:
            self._record(tag_, messages, prompt, kwargs, None, None, t0,
                         f"{e.__class__.__name__}: {e}")
            raise
        usage = None
        content = ""
        finish = None
        try:
            u = resp.usage
            usage = {"prompt_tokens": u.prompt_tokens,
                     "completion_tokens": u.completion_tokens,
                     "total_tokens": u.total_tokens}
            content = str(resp.choices[0].message.content or "")
            finish = resp.choices[0].finish_reason
        except (AttributeError, IndexError, TypeError):
            pass
        self._record(tag_, messages, prompt, kwargs, resp if self.trace_full else None,
                     {"content": content, "finish_reason": finish, "usage": usage}, t0, None)
        return resp

    def _record(self, tag_, messages, prompt, kwargs, full_resp, brief, t0, error) -> None:
        if self.trace is None:
            return
        msgs = messages or ([{"role": "user", "content": prompt}] if prompt else [])
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        full_path = None
        if self.trace_full and full_resp is not None:
            full_path = str(self.trace.write_full(
                tag_ or "untagged",
                {"request": {"messages": msgs, **{k: v for k, v in kwargs.items()
                                                 if isinstance(v, (int, float, str, bool))}},
                 "response": json.loads(full_resp.json())
                 if hasattr(full_resp, "json") else str(full_resp)}))
        brief = brief or {}
        response_summary = None
        if brief:
            response_summary = {"finish_reason": brief.get("finish_reason")}
            response_summary.update(
                _summarize_content(brief.get("content") or "",
                                   _RESPONSE_HEAD_CHARS, _RESPONSE_TAIL_CHARS))
        self.trace.record({
            "ts": datetime.now().isoformat(timespec="milliseconds"),
            "kind": "chat",
            "tag": tag_,
            "model": self.model,
            "request_summary": {
                "messages": _summarize_messages(msgs),
                "max_tokens": kwargs.get("max_tokens"),
                "total_chars": sum(len(str(m.get("content") or "")) for m in msgs),
            },
            "response_summary": response_summary,
            "usage": (brief or {}).get("usage"),
            "metrics": {
                "elapsed_ms": elapsed_ms,
                "request_chars": sum(len(str(m.get("content") or "")) for m in msgs),
                "response_chars": len((brief or {}).get("content") or ""),
            },
            "error": error,
            "full_path": full_path,
        })


def connect(trace_path: Path | None = None, trace_full: bool = False) -> TracedLM:
    """连接本地 llama-server 并 dspy.configure；服务不可达抛 requests 异常。"""
    _bypass_env_proxy()
    r = requests.get(f"{LLM_BASE}/models", timeout=30)
    r.raise_for_status()
    model = r.json()["data"][0]["id"]
    lm = TracedLM(f"openai/{model}", api_base=LLM_BASE, api_key="local",
                  temperature=0.3, cache=False, num_retries=3,
                  timeout=600, max_tokens=4000,
                  extra_body={"chat_template_kwargs": {"enable_thinking": False}},
                  trace_path=trace_path, trace_full=trace_full)
    dspy.configure(lm=lm)
    return lm


def try_connect(trace_path: Path | None = None, trace_full: bool = False) -> TracedLM | None:
    """不抛异常的建连方式：服务不可用返回 None（真实原因写日志，别让调用方猜）。"""
    try:
        return connect(trace_path=trace_path, trace_full=trace_full)
    except (requests.exceptions.RequestException, KeyError, IndexError) as e:
        log.error("连不上 LLM 服务 %s：%s: %s", LLM_BASE, type(e).__name__, e)
        return None


def _summarize_content(content: str, head: int, tail: int) -> dict:
    out: dict = {"len": len(content)}
    if content:
        out["head"] = content[:head]
    if len(content) > head + tail:
        out["tail"] = content[-tail:]
    return out