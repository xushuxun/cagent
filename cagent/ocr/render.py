"""PDF → 页图渲染：pypdfium2 渲染、内容旋转矫正。

渲染在主线程串行（PDFium 非线程安全），每页产出 PNG 字节交给下游 OvisOCR2
识别。渲染倍率 scale=2.0（≈144 DPI），不做长边封顶——服务端 processor 按
max_pixels=2880×2880 自行降采样。
"""

import io
import logging
import math

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_raw
from PIL import Image

log = logging.getLogger("ocr.render")

RENDER_SCALE = 2.0  # 渲染倍率（≈144 DPI；scale = 渲染像素 / PDF point）

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


def render_page_png(doc: pdfium.PdfDocument, page_index: int) -> bytes:
    """渲染单页为 PNG（CPU 密集，在主线程串行调用；PDFium 非线程安全）。

    固定 scale=2.0，超大页由服务端 processor 按 max_pixels 降采样。
    """
    page = doc[page_index]
    try:
        # pypdfium2 官方文档 scale 为 float（DPI/72 换算系数），其类型 stub 误标为 int
        pil_image = page.render(scale=RENDER_SCALE).to_pil()  # pyright: ignore[reportArgumentType]
        buf = io.BytesIO()
        pil_image.save(buf, format="PNG")
        return correct_rotation(page, buf.getvalue(), page_index + 1)
    finally:
        page.close()
