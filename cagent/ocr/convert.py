"""PDF → Markdown 解析核心：渲染、旋转矫正、请求模型、grounding 清理、合并。

单份文档内部是生产—消费流水线——主线程逐页渲染图片（CPU 密集，PDFium 非线程安全只能
串行），每完成一页立刻把该页 OCR 任务提交到线程池（网络 IO，限流并发），两阶段重叠
执行；收口时按页序合并再原子写入，中断不会留下半截文件。文档之间串行，避免同时抢占
显存与请求配额（见 batch.py 与 lib/service.py）。
"""

import ast
import base64
import io
import logging
import math
import re
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_raw
import requests
from PIL import Image
from requests.adapters import HTTPAdapter

from cagent.lib.lakehouse import page_failure_text
from cagent.ocr.pending import atomic_write

log = logging.getLogger("ocr.convert")

BASE_URL = "http://localhost:8000"
MODEL = "Unlimited-OCR"
TIMEOUT = 60
PAGE_MAX_ATTEMPTS = 3    # 单页最大尝试次数
PAGE_CONCURRENCY = 8     # 页面并发请求数
PAGE_MAX_PENDING = 32    # 渲染线程最多排队页数（含执行中），防止整本 PDF 图片驻留内存
RENDER_DPI = 300         # 渲染 DPI
MAX_TOKENS = 4096
RETRY_TEMPERATURE_STEP = 0.1  # 打满 max_tokens 每次重试的温度增量（0 为确定性，小幅升温跳出退化循环）
EXTRACT_IMAGES = False   # 是否按检测框裁剪保存页面图片（默认关闭，image 占位符整体删除）

REF_DET_PATTERN = re.compile(
    r"<\|ref\|>(.*?)<\|/ref\|>\s*<\|det\|>(.*?)<\|/det\|>", re.DOTALL
)
DET_PATTERN = re.compile(
    r"<\|det\|>\s*([A-Za-z_][\w-]*)\s*(.*?)\s*<\|/det\|>", re.DOTALL
)
REF_PATTERN = re.compile(r"<\|ref\|>(.*?)<\|/ref\|>", re.DOTALL)
ORPHAN_DET_PATTERN = re.compile(r"<\|det\|>.*?<\|/det\|>", re.DOTALL)


# 内容旋转角度（顺时针）-> PIL 矫正角度（逆时针），dir(0,-1) 需顺时针转 90° 即 rotate(270)
CORRECT_ANGLE = {0: 0, 90: 90, 180: 180, 270: 270}


def content_rotation(page: pdfium.PdfPage) -> tuple[int | None, float]:
    """检测页面文本内容的主方向。返回 (旋转角度, 占比)；无文本时返回 (None, 0)。

    用 pdfium raw FPDFText_GetCharAngle（字符基线弧度角，deg % 360 即内容顺时针角）。
    """
    weights: dict[int, int] = {0: 0, 90: 0, 180: 0, 270: 0}
    textpage = page.get_textpage()
    try:
        for i in range(textpage.count_chars()):
            if not textpage.get_text_range(i, 1).strip():
                continue
            degrees = math.degrees(pdfium_raw.FPDFText_GetCharAngle(textpage.raw, i))
            weights[round(degrees / 90) * 90 % 360] += 1
    finally:
        textpage.close()
    total = sum(weights.values())
    if total == 0:
        return None, 0.0
    angle = max(weights, key=lambda a: weights[a])  # type: ignore[arg-type]
    return angle, weights[angle] / total


def correct_rotation(page: pdfium.PdfPage, png_bytes: bytes, page_number: int) -> bytes:
    """页面 /Rotate 为 0 但正文内容旋转时（如竖排表格页），旋转图像矫正。"""
    angle = CORRECT_ANGLE.get(content_rotation(page)[0] or 0, 0)
    if not angle:
        return png_bytes
    with Image.open(io.BytesIO(png_bytes)) as img:
        buf = io.BytesIO()
        img.rotate(angle, expand=True).save(buf, format="PNG")
    log.info("第 %d 页内容旋转，已矫正 %d°", page_number, angle)
    return buf.getvalue()


def render_page_png(doc: pdfium.PdfDocument, page_index: int, dpi: int) -> bytes:
    """渲染单页为 PNG（CPU 密集，在主线程串行调用；PDFium 非线程安全）。"""
    page = doc[page_index]
    try:
        # pypdfium2 官方文档 scale 为 float（DPI/72 换算系数），其类型 stub 误标为 int
        pil_image = page.render(scale=dpi / 72).to_pil()  # pyright: ignore[reportArgumentType]
        buf = io.BytesIO()
        pil_image.save(buf, format="PNG")
        return correct_rotation(page, buf.getvalue(), page_index + 1)
    finally:
        page.close()


def request_page_markdown(
    session: requests.Session, img_base64: str, temperature: float = 0.0
) -> tuple[str, str]:
    """非流式请求单页解析结果，返回 (内容, finish_reason)。

    finish_reason == "length" 表示打满 max_tokens，通常是模型死循环或内容超长，
    调用方应提高 temperature 重试以跳出确定性退化循环。
    """
    payload = {
        "model": MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "<image>document parsing."},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_base64}"}},
                ],
            }
        ],
        "max_tokens": MAX_TOKENS,
        "temperature": temperature,
        "skip_special_tokens": False,
        "stream": False,
        "vllm_xargs": {"ngram_size": 35, "window_size": 128},
    }
    response = session.post(f"{BASE_URL}/v1/chat/completions", json=payload, timeout=TIMEOUT)
    response.raise_for_status()
    choice = response.json()["choices"][0]
    return choice["message"]["content"], choice.get("finish_reason", "")


def clean_grounded_markdown(
    raw_markdown: str,
    page_image_bytes: bytes,
    images_dir: Path,
    images_link_prefix: str,
    page_number: int,
) -> tuple[str, int]:
    """清理模型输出中的 ref/det 占位符，可选按检测框从页面图像裁剪图片。

    EXTRACT_IMAGES=False（默认）时不裁剪保存图片，image 检测框占位符整体删除；
    True 时裁剪落盘并在原地插入相对链接。返回 (清理后的 Markdown, 裁剪出的图片数量)。
    """
    extracted_count = 0

    if EXTRACT_IMAGES:
        images_dir.mkdir(parents=True, exist_ok=True)
        with Image.open(io.BytesIO(page_image_bytes)) as page_image:
            page_image = page_image.convert("RGB")

            def replace_grounding(match: re.Match[str]) -> str:
                nonlocal extracted_count
                label = match.group(1).strip().lower()
                if label != "image":
                    return ""
                links: list[str] = []
                for box in _parse_boxes(match.group(2)):
                    crop = _crop_box(page_image, box)
                    if crop is None:
                        continue
                    extracted_count += 1
                    filename = f"page-{page_number:04d}-image-{extracted_count:03d}.jpg"
                    crop.save(images_dir / filename, format="JPEG", quality=92, optimize=True)
                    links.append(f"![第 {page_number} 页图片 {extracted_count}]({images_link_prefix}/{filename})")
                return "\n".join(links)

            cleaned = REF_DET_PATTERN.sub(replace_grounding, raw_markdown)
            cleaned = DET_PATTERN.sub(replace_grounding, cleaned)
    else:
        cleaned = REF_DET_PATTERN.sub("", raw_markdown)
        cleaned = DET_PATTERN.sub("", cleaned)

    cleaned = REF_PATTERN.sub(lambda match: match.group(1), cleaned)
    cleaned = ORPHAN_DET_PATTERN.sub("", cleaned)
    # 兜底：配对消费后仍残存的孤立 grounding 标签（表格行尾裂开/截断时会出现单独一个
    # <|det|> 之类，无配对信息可保留），一律删除，保证产物零残留
    cleaned = re.sub(r"<\|[^|]*\|>", "", cleaned)
    cleaned = cleaned.replace("<PAGE>", "").replace("", "")
    cleaned = cleaned.replace("\\coloneqq", ":=").replace("\\eqqcolon", "=:")
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip(), extracted_count


def _parse_boxes(value: str) -> list[tuple[float, float, float, float]]:
    """解析 det 标签中的坐标框，支持单个框 [x1,y1,x2,y2] 或框列表。"""
    try:
        parsed = ast.literal_eval(value.strip())
    except (SyntaxError, ValueError):
        return []
    if isinstance(parsed, (list, tuple)) and len(parsed) == 4 and all(
        isinstance(item, (int, float)) for item in parsed
    ):
        parsed = [parsed]
    boxes: list[tuple[float, float, float, float]] = []
    if not isinstance(parsed, (list, tuple)):
        return boxes
    for item in parsed:
        if isinstance(item, (list, tuple)) and len(item) == 4 and all(
            isinstance(value, (int, float)) for value in item
        ):
            boxes.append(tuple(float(value) for value in item))  # type: ignore
    return boxes


def _crop_box(
    image: Image.Image, box: tuple[float, float, float, float]
) -> Image.Image | None:
    """按 0-999 归一化坐标裁剪图像，坐标越界或空框时返回 None。"""
    width, height = image.size
    x1, y1, x2, y2 = box
    left = max(0, min(width, round(x1 / 999 * width)))
    top = max(0, min(height, round(y1 / 999 * height)))
    right = max(0, min(width, round(x2 / 999 * width)))
    bottom = max(0, min(height, round(y2 / 999 * height)))
    if right <= left or bottom <= top:
        return None
    return image.crop((left, top, right, bottom))


def _merge_pages(pages: list[str], source_filename: str) -> str:
    """将各页 Markdown 合并为单个文档，页间用 `<!-- page N -->` 分隔（lakehouse 约定）。"""
    safe_source = source_filename.replace("--", "").replace(">", "")
    sections = [f"<!-- Source: {safe_source} -->"]
    for page_number, markdown in enumerate(pages, start=1):
        sections.append(f"<!-- page {page_number} -->\n\n{markdown}")
    return "\n\n".join(sections).strip() + "\n"


def process_page(
    page_number: int,
    img_bytes: bytes,
    img_base64: str,
    session: requests.Session,
    images_dir: Path,
    images_link_prefix: str,
) -> tuple[str, int]:
    """解析单页：请求模型、异常重试、Markdown 后处理。返回 (页面 Markdown, 裁剪图片数)。

    打满 max_tokens（finish_reason == "length"）视为失败：截断内容不收下，每次重试
    temperature 累加 RETRY_TEMPERATURE_STEP（temperature=0 是确定性的，原样重试必然
    复现，小幅升温跳出退化循环）；最后一次仍打满则降级收下截断内容并告警。
    """
    raw_markdown: str | None = None
    for attempt in range(1, PAGE_MAX_ATTEMPTS + 1):
        try:
            temperature = round((attempt - 1) * RETRY_TEMPERATURE_STEP, 2)
            raw_markdown, finish_reason = request_page_markdown(
                session, img_base64, temperature=temperature
            )
            if finish_reason == "length":
                if attempt < PAGE_MAX_ATTEMPTS:
                    next_temperature = round(attempt * RETRY_TEMPERATURE_STEP, 2)
                    log.warning(
                        "第 %d 页输出打满 max_tokens（%d），temperature %.2f→%.2f 重试"
                        "（第 %d/%d 次尝试）",
                        page_number, MAX_TOKENS, temperature, next_temperature,
                        attempt, PAGE_MAX_ATTEMPTS,
                    )
                    continue
                log.warning(
                    "第 %d 页重试后仍打满 max_tokens（%d），已收下截断内容，本页可能缺失尾部",
                    page_number, MAX_TOKENS,
                )
            break
        except Exception as exc:
            if attempt < PAGE_MAX_ATTEMPTS:
                log.warning("第 %d 页解析失败（第 %d/%d 次尝试）: %s", page_number, attempt, PAGE_MAX_ATTEMPTS, exc)
            else:
                log.error("第 %d 页重试后仍失败，已跳过: %s", page_number, exc)

    if raw_markdown is None:
        return page_failure_text(page_number), 0

    markdown, extracted = clean_grounded_markdown(
        raw_markdown, img_bytes, images_dir, images_link_prefix, page_number
    )
    return markdown, extracted


def process_pdf(pdf_path: Path, company_dir: Path) -> bool:
    """解析单份 PDF，Markdown 写入 derived/，裁剪图片写入 derived/images/<同名>/。

    流水线：主线程逐页渲染（CPU 密集，PDFium 串行），每完成一页立即把 OCR 任务
    提交到线程池（网络 IO，限流并发），渲染与请求重叠执行。
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
    # 16 线程共用 session：默认连接池只有 10，池满会丢弃连接并重复建连
    # （“Connection pool is full”警告就是它）。池大小对齐页面并发数。
    adapter = HTTPAdapter(
        pool_connections=PAGE_CONCURRENCY, pool_maxsize=PAGE_CONCURRENCY)
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    def run_page(page_number: int, img_bytes: bytes, img_base64: str):
        page_markdown[page_number - 1], _ = process_page(
            page_number, img_bytes, img_base64, session, images_dir, images_link_prefix
        )

    with ThreadPoolExecutor(max_workers=PAGE_CONCURRENCY) as executor:
        pending: list[Future[None]] = []
        try:
            for page_index in range(page_count):
                img_bytes = render_page_png(doc, page_index, RENDER_DPI)
                img_base64 = base64.b64encode(img_bytes).decode("utf-8")
                pending.append(executor.submit(run_page, page_index + 1, img_bytes, img_base64))
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