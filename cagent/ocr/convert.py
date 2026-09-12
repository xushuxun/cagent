"""PDF → Markdown 解析编排：渲染 → OvisOCR2 端到端整页识别（vLLM 服务）→ 拼页落盘。

OvisOCR2 单模型端到端逐页输出整页 markdown（表格 HTML、公式 LaTeX、图表
bbox 占位）。主线程逐页渲染（PDFium 非线程安全，串行），每页的识别请求
（网络 IO）提交线程池并发执行；异常判定只看 finish_reason：打满 max_tokens
（finish_reason=="length"）视为异常，升温重试跳出退化输出；其余异常同样重试。
单页重试耗尽写占位，文档结束原子落盘。文档之间串行（batch.py 编排）。

溯源：产物按 lakehouse 约定带 `<!-- page N -->` 物理页标记，下游按页定位引用（p.N）。
模型对图表输出 `<img src="images/bbox_{left}_{top}_{right}_{bottom}.jpg" />` 占位
（0–1000 归一化坐标），物化为真实裁剪图：写入 derived/images/<文档名>/，
链接改写为相对 derived/ 的路径。
"""

import base64
import io
import logging
import re
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path

import pypdfium2 as pdfium
import requests
from PIL import Image
from requests.adapters import HTTPAdapter

from cagent.ocr.render import render_page_png

log = logging.getLogger("ocr.convert")

BASE_URL = "http://localhost:8000"
MODEL = "OvisOCR2"
TIMEOUT = 600
PAGE_MAX_ATTEMPTS = 3      # 单页最大尝试次数
PAGE_CONCURRENCY = 4       # 页面并发请求数（vLLM 服务端排队，客户端少量并发即可）
PAGE_MAX_PENDING = 8       # 渲染线程最多排队页数（含执行中），防止整本 PDF 图片驻留内存
MAX_TOKENS = 16384         # 单页输出上限；需 vLLM --max-model-len ≥ 图片 token + 本值
RETRY_TEMPERATURE_STEP = 0.1  # 打满 max_tokens 每次重试的温度增量（0 为确定性，小幅升温跳出退化循环）

EXTRACT_IMAGES = False  # 是否裁剪保存图表图片（--extract-images 开启）

OCR_PROMPT = (
    "\nExtract all readable content from the image in natural human reading order "
    "and output the result as a single Markdown document. For charts or images, "
    'represent them using an HTML image tag: <img src="images/bbox_{left}_{top}_{right}_{bottom}.jpg" />, '
    "where left, top, right, bottom are bounding box coordinates scaled to [0, 1000). "
    "Format formulas as LaTeX. Format tables as HTML: <table>...</table>. "
    "Transcribe all other text as standard Markdown. Preserve the original text "
    "without translation or paraphrasing."
)


def page_failure_text(page: int) -> str:
    """第 page 页解析失败时写进解析全文的占位符（报告端按同一文本识别缺失页）。"""
    return f"> ⚠️ 第 {page} 页解析失败，本页内容缺失。"


def atomic_write(path: Path, data: str) -> None:
    """先写临时文件再改名，避免进程中断留下半截文件。"""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(data, encoding="utf-8")
    tmp.replace(path)


BBOX_IMAGE_PATTERN = re.compile(
    r'<img\s+src=["\']images/bbox_(\d+)_(\d+)_(\d+)_(\d+)\.jpg["\']\s*/?>',
    flags=re.IGNORECASE,
)


def materialize_bbox_images(
    markdown: str,
    page_image: Image.Image,
    page_number: int,
    images_dir: Path,
    images_link_prefix: str,
    extract_images: bool = False,
) -> str:
    """把模型输出里的 bbox 图片占位替换为真实裁剪图。

    坐标为 0–1000 归一化值，按页图像素换算裁剪；裁剪缩至 1200px 内、JPEG q85
    落盘 derived/images/<文档名>/，链接改写为相对 derived/ 的路径。退化框
    （坐标为空区域）保留原占位不动。extract_images=False 时不裁剪不落盘，
    占位原样保留（产物不引用任何图片文件）。
    """
    if not extract_images:
        return markdown
    width, height = page_image.size
    images_dir.mkdir(parents=True, exist_ok=True)
    counter = [0]  # 列表绕开闭包内层赋值；同页内串行，无需线程安全

    def replace(match: re.Match[str]) -> str:
        left, top, right, bottom = (int(value) for value in match.groups())
        x1 = max(0, min(width, round(left * width / 1000)))
        y1 = max(0, min(height, round(top * height / 1000)))
        x2 = max(0, min(width, round(right * width / 1000)))
        y2 = max(0, min(height, round(bottom * height / 1000)))
        if x2 <= x1 or y2 <= y1:
            return match.group(0)

        crop = page_image.crop((x1, y1, x2, y2)).convert("RGB")
        crop.thumbnail((1200, 1200), Image.Resampling.BILINEAR)
        index = counter[0]
        counter[0] += 1
        filename = f"page-{page_number:04d}-{index:03d}.jpg"
        buf = io.BytesIO()
        crop.save(buf, format="JPEG", quality=85, optimize=False)
        (images_dir / filename).write_bytes(buf.getvalue())
        return f'<img src="{images_link_prefix}/{filename}" />'

    return BBOX_IMAGE_PATTERN.sub(replace, markdown)


def recognize_page(
    session: requests.Session, img_base64: str, temperature: float
) -> tuple[str, str]:
    """非流式请求单页识别，返回 (内容, finish_reason)。

    finish_reason == "length" 表示打满 max_tokens，调用方应升温重试。
    """
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
    response = session.post(f"{BASE_URL}/v1/chat/completions", json=payload, timeout=TIMEOUT)
    response.raise_for_status()
    choice = response.json()["choices"][0]
    return choice["message"]["content"], choice.get("finish_reason", "")


def _recognize_with_retry(
    session: requests.Session, page_number: int, img_bytes: bytes
) -> str | None:
    """单页识别重试循环：打满 max_tokens 或请求异常都重试，temperature 累加；
    耗尽返回 None（调用方写占位）。"""
    img_base64 = base64.b64encode(img_bytes).decode("utf-8")
    content: str | None = None
    for attempt in range(1, PAGE_MAX_ATTEMPTS + 1):
        try:
            temperature = round((attempt - 1) * RETRY_TEMPERATURE_STEP, 2)
            content, finish_reason = recognize_page(session, img_base64, temperature)
            if finish_reason == "length":
                if attempt < PAGE_MAX_ATTEMPTS:
                    log.warning(
                        "第 %d 页打满 max_tokens（%d），temperature %.2f→%.2f 重试"
                        "（第 %d/%d 次尝试）",
                        page_number, MAX_TOKENS, temperature,
                        round(attempt * RETRY_TEMPERATURE_STEP, 2), attempt, PAGE_MAX_ATTEMPTS,
                    )
                    continue
                log.warning(
                    "第 %d 页重试后仍打满 max_tokens（%d），已收下截断内容，本页可能缺失尾部",
                    page_number, MAX_TOKENS,
                )
            return content
        except Exception as exc:
            if attempt < PAGE_MAX_ATTEMPTS:
                log.warning("第 %d 页识别失败（第 %d/%d 次尝试）: %s",
                            page_number, attempt, PAGE_MAX_ATTEMPTS, exc)
            else:
                log.error("第 %d 页重试后仍失败，本页写占位: %s", page_number, exc)
    return None


def _merge_pages(pages: list[str], source_filename: str) -> str:
    """将各页 Markdown 合并为单个文档，页间用 `<!-- page N -->` 分隔（lakehouse 约定）。"""
    safe_source = source_filename.replace("--", "").replace(">", "")
    sections = [f"<!-- Source: {safe_source} -->"]
    for page_number, markdown in enumerate(pages, start=1):
        sections.append(f"<!-- page {page_number} -->\n\n{markdown}")
    return "\n\n".join(sections).strip() + "\n"


def process_pdf(pdf_path: Path, company_dir: Path,
                extract_images: bool = EXTRACT_IMAGES) -> bool:
    """解析单份 PDF，Markdown 写入 derived/（同名 .md，原子落盘）。

    流水线：主线程逐页渲染（串行），每页的识别请求提交线程池并发执行，
    收口按页序合并。extract_images=True 时额外裁剪图表落盘 derived/images/。
    """
    derived_dir = company_dir / "derived"
    derived_dir.mkdir(parents=True, exist_ok=True)
    md_path = derived_dir / (pdf_path.stem + ".md")
    # 图片按文档分目录，避免跨 PDF 同名冲突；md 中链接为相对 derived/ 的路径
    images_dir = derived_dir / "images" / pdf_path.stem
    images_link_prefix = f"images/{pdf_path.stem}"

    doc = pdfium.PdfDocument(str(pdf_path))
    page_count = len(doc)
    if page_count == 0:
        doc.close()
        log.error("[FAIL] PDF 无页面: %s", pdf_path)
        return False

    page_markdown: list[str] = [""] * page_count
    session = requests.Session()
    session.trust_env = False
    session.verify = False
    # 多线程共用 session：默认连接池只有 10，池满会丢弃连接并重复建连
    # （“Connection pool is full”警告就是它）。池大小对齐页面并发数。
    adapter = HTTPAdapter(
        pool_connections=PAGE_CONCURRENCY, pool_maxsize=PAGE_CONCURRENCY)
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    def run_page(page_number: int, img_bytes: bytes):
        try:
            markdown = _recognize_with_retry(session, page_number, img_bytes)
            if markdown is None:
                page_markdown[page_number - 1] = page_failure_text(page_number)
                return
            with Image.open(io.BytesIO(img_bytes)) as page_image:
                page_markdown[page_number - 1] = materialize_bbox_images(
                    markdown, page_image.convert("RGB"), page_number,
                    images_dir, images_link_prefix, extract_images,
                )
        except Exception as exc:
            log.error("第 %d 页解析失败，写占位: %s", page_number, exc)
            page_markdown[page_number - 1] = page_failure_text(page_number)

    with ThreadPoolExecutor(max_workers=PAGE_CONCURRENCY) as executor:
        pending: list[Future[None]] = []
        try:
            for page_index in range(page_count):
                img_bytes = render_page_png(doc, page_index)
                pending.append(executor.submit(run_page, page_index + 1, img_bytes))
                # 背压：主线程不再无限渲染排队，超过水位就先收一批已完成页
                if len(pending) >= PAGE_MAX_PENDING:
                    done, _ = wait(pending, return_when=FIRST_COMPLETED)
                    for future in done:
                        future.result()
                        pending.remove(future)
        finally:
            doc.close()
        while pending:
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                future.result()
                pending.remove(future)

    session.close()
    atomic_write(md_path, _merge_pages(page_markdown, pdf_path.name))
    return True
