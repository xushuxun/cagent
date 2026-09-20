"""OCR cli：

    # 解析全部年报；重跑已解析的加 --force
    uv run cagent/ocr/cli.py --stock 09863 --market hk

    # 顺带裁剪图表落盘 derived/images/
    uv run cagent/ocr/cli.py --stock 600519 --market cn --extract-images

幂等断点续跑：derived/ 已有的产物自动跳过，重跑同一命令即可续跑。
批量全市场请外层脚本循环调本命令。
"""

import argparse
import base64
import logging
import sys
from pathlib import Path

from cagent.ocr.llm_utils import connect_llm, request_llm_batch
from cagent.ocr.pdf_utils import render_pdf_to_images

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stderr,
)
logging.getLogger("httpx2").setLevel(logging.WARNING)
log = logging.getLogger("ocr")


def pdf_to_md(pdf_path: Path) -> str:
    client = connect_llm()

    images = render_pdf_to_images(pdf_path)
    images_base64 = [base64.b64encode(image).decode("utf-8") for image in images]

    md_pages = request_llm_batch(client, images_base64)

    md_content = "\n".join(f"<!-- page {i + 1} -->\n{md_page}" for i, md_page in enumerate(md_pages))
    return md_content


def ocr_pdf_in_lakehouse() -> None:
    pdfs_dir = Path(args.root) / str(args.market) / str(args.stock)

    pdfs = list(pdfs_dir.glob("*.pdf", case_sensitive=False))
    if not pdfs_dir.exists() or not pdfs:
        log.error(f"{pdfs_dir} 不存在，请先下载年报 PDF")
        sys.exit(1)

    md_dir = pdfs_dir / "derived"
    md_dir.mkdir(parents=True, exist_ok=True)

    if args.extract_images:
        images_dir = md_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)

    mds = [md_dir / (pdf.stem + ".md") for pdf in pdfs]
    need_to_parse_pdfs = [pdf for pdf, md in zip(pdfs, mds) if not md.exists()]
    if not need_to_parse_pdfs:
        log.info("已解析全部 PDF")
        sys.exit(0)

    log.info(f"待解析 PDF 数量: {len(need_to_parse_pdfs)}")

    for pdf_path in need_to_parse_pdfs:
        log.info(f"解析 PDF: {pdf_path}")
        md_content = pdf_to_md(pdf_path)
        md_path = md_dir / (pdf_path.stem + ".md")
        md_path.write_text(md_content, encoding="utf-8")
        log.info(f"输出 Markdown: {md_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--stock", help="股票代码，如 09863、600519")
    parser.add_argument("--market", choices=["cn", "hk"], help="目标市场")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[2] / ".cagent"))
    parser.add_argument("--extract-images", action="store_true")
    parser.add_argument("-i", "--input", help="单文件调试模式，指定 PDF 文件路径")
    args = parser.parse_args()

    if args.input:
        pdf_path = Path(args.input)
        if not pdf_path.exists():
            log.error(f"{pdf_path} 不存在")
            sys.exit(1)
        log.info(f"单文件调试模式，解析 PDF: {pdf_path}")
        md_content = pdf_to_md(pdf_path)
        output_path = pdf_path.with_suffix(".md")
        output_path.write_text(md_content, encoding="utf-8")
        log.info(f"输出 Markdown: {output_path}")
    else:
        ocr_pdf_in_lakehouse()
