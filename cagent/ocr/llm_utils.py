
import asyncio

import httpx2

BASE_URL = "http://localhost:8000"
MODEL = "OvisOCR2"
TIMEOUT = 60
MAX_TOKENS =8192 
PAGE_CONCURRENCY = 16

OCR_PROMPT = (
    "\nExtract all readable content from the image in natural human reading order "
    "and output the result as a single Markdown document. For charts or images, "
    'represent them using an HTML image tag: <img src="images/bbox_{left}_{top}_{right}_{bottom}.jpg" />, '
    "where left, top, right, bottom are bounding box coordinates scaled to [0, 1000). "
    "Format formulas as LaTeX. Format tables as HTML: <table>...</table>. "
    "Transcribe all other text as standard Markdown. Preserve the original text "
    "without translation or paraphrasing."
)


def connect_llm(base_url: str = BASE_URL) -> httpx2.AsyncClient:
    """检查 vLLM 服务是否可用。"""
    response = httpx2.get(f"{base_url}/health", timeout=1)
    response.raise_for_status()
    return httpx2.AsyncClient(base_url=base_url, trust_env=False, verify=False, timeout=TIMEOUT)

async def request_llm(client: httpx2.AsyncClient, img_base64: str, temperature: float = 0.0) -> str:
    payload = {
        "model": MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_base64}"}},
                    {"type": "text", "text": OCR_PROMPT},
                ],
            }
        ],
        "max_tokens": MAX_TOKENS,
        "temperature": temperature,
        "stream": False,
    }
    response = await client.post("/v1/chat/completions", json=payload)
    response.raise_for_status()
    choice = response.json()["choices"][0]
    finish_reason = choice.get("finish_reason", "")
    if finish_reason == "length":
        return "<!-- reach max_tokens -->"

    return choice["message"]["content"]

async def _request_llm_bounded(semaphore: asyncio.Semaphore, client: httpx2.AsyncClient, img_base64: str) -> str:
    async with semaphore:
        return await request_llm(client, img_base64)

def request_llm_batch(client: httpx2.AsyncClient, images_base64: list) -> list[str]:
    """批量请求 LLM，返回结果列表，并发上限为 PAGE_CONCURRENCY。"""
    async def _run() -> list[str]:
        semaphore = asyncio.Semaphore(PAGE_CONCURRENCY)
        tasks = [_request_llm_bounded(semaphore, client, img_base64) for img_base64 in images_base64]
        return await asyncio.gather(*tasks)

    return asyncio.run(_run())