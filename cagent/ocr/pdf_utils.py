import io
import math
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_raw


def _content_rotation(page: pdfium.PdfPage) -> int | None:
    """检测页面正文的主方向：返回 0/90/180/270 中字符最多的角度；无文本时返回 None。

    用 pdfium raw FPDFText_GetCharAngle（字符基线弧度角，deg % 360 即内容顺时针角）。
    """
    weights: dict[int, int] = {0: 0, 90: 0, 180: 0, 270: 0}
    textpage = page.get_textpage()
    for i in range(textpage.count_chars()):
        if not textpage.get_text_range(i, 1).strip():
            continue
        degrees = math.degrees(pdfium_raw.FPDFText_GetCharAngle(textpage.raw, i))
        weights[round(degrees / 90) * 90 % 360] += 1
    if not any(weights.values()):
        return None
    return max(weights, key=lambda a: weights[a])  # type: ignore[arg-type]


def render_pdf_to_images(pdf_path: Path) -> list[bytes]:
    pdf = pdfium.PdfDocument(pdf_path)
    
    images = []
    for i in range(len(pdf)):
        page = pdf[i]
        angle = _content_rotation(page) or 0
        # PDF文档内部使用“点”（point）作为坐标和尺寸的默认单位。
        # 在标准PDF规范中，1个点严格等于 1/72 英寸，这意味着PDF的默认逻辑分辨率是 72 DPI (Dots Per Inch)。
        # 因此，scale=1.0 就代表渲染时保持1点映射为1像素，即输出图像的分辨率为72 DPI。
        pil_image = page.render(scale=2, rotation=angle).to_pil() 
        buf = io.BytesIO()
        pil_image.save(buf, format="PNG")
        png = buf.getvalue()
        images.append(png)

    return images
        