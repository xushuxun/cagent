"""LLM 请求的 trace 记录：JSONL 追加 + 可选全量落盘 + 线程级 tag。"""

import json
import threading
from contextlib import contextmanager
from pathlib import Path

_REQUEST_HEAD_CHARS = 200
_REQUEST_TAIL_CHARS = 100
_RESPONSE_HEAD_CHARS = 500
_RESPONSE_TAIL_CHARS = 200


class TraceSink:
    """把每次 LLM 请求追加为一行 JSONL；trace_full 时完整 request/response 另存。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.full_dir = self.path.parent / "full" / self.path.stem
        self._lock = threading.Lock()
        self._counter = 0

    def record(self, event: dict) -> None:
        line = json.dumps(event, ensure_ascii=False)
        with self._lock, self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def write_full(self, tag: str, payload: dict) -> Path:
        with self._lock:
            self._counter += 1
            self.full_dir.mkdir(parents=True, exist_ok=True)  # 只在 --full 时才建目录
            path = self.full_dir / f"{self._counter:06d}_{tag}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        return path


def _summarize_content(content: str, head: int, tail: int) -> dict:
    out: dict = {"len": len(content)}
    if content:
        out["head"] = content[:head]
    if len(content) > head + tail:
        out["tail"] = content[-tail:]
    return out


def _summarize_messages(messages: list[dict]) -> list[dict]:
    out = []
    for i, m in enumerate(messages):
        content = str(m.get("content") or "")
        item = {"index": i, "role": m.get("role", "unknown")}
        item.update(_summarize_content(content, _REQUEST_HEAD_CHARS, _REQUEST_TAIL_CHARS))
        out.append(item)
    return out


_tls = threading.local()


@contextmanager
def tag(name: str):
    """设置当前线程的 trace tag，with 块内的 LLM 请求都记这个 tag。"""
    prev = getattr(_tls, "tag", "")
    _tls.tag = name
    try:
        yield
    finally:
        _tls.tag = prev


def current_tag() -> str:
    return getattr(_tls, "tag", "")