r"""单公司端到端流水线：下载年报 → vLLM OCR → llama-server 报告生成。

纯编排：不 import 任何 cagent 代码，每个阶段以子进程方式调用 cagent 内的入口脚本：
    1. 下载该公司年报：cagent/scraper/cli.py --stock <code> --market <market>
    2. PDF 转 markdown：cagent/ocr/cli.py（vLLM 由该入口自管：在跑复用、没有拉起跑完即关）
    3. 报告生成：cagent/report.py（llama-server 同样自管；TOC 缺失时 agent 内部补建）

各阶段幂等可断点续跑：已下载的 PDF、已解析的 derived/、已生成的 .pageindex.json
都会自动跳过；ocr/pageindex 入口在无活可做时直接退出，不启动推理服务。

服务启动命令与生命周期只在 cagent/lib/service.py 定义（入口脚本与 tests 自管
共用同一套），本文件不重复实现。

用法（在仓库根运行）:
    uv run python cagent/pipeline.py --market hk --code 09863
    uv run python cagent/pipeline.py --market hk --code 09863 --trace
"""

import argparse
import logging
import os
import shlex
import subprocess
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stderr,
)
log: logging.Logger = logging.getLogger("pipeline")

CAGENT = Path(__file__).resolve().parent
ROOT = CAGENT.parent
DEFAULT_LAKEHOUSE = ROOT / ".cagent"
SERVICE_STATE_DIR = ROOT / "output" / ".services"


def run_step(cmd: list[str]) -> None:
    """以子进程跑一个阶段；失败抛 CalledProcessError，由 main 统一收口。"""
    log.info("运行: %s", " ".join(shlex.quote(c) for c in cmd))
    subprocess.run(cmd, check=True)


def step_download(market: str, code: str, lakehouse: Path,
                  date_from: str, date_to: str, force: bool) -> None:
    log.info("=== 阶段 1/3: 下载年报 %s %s ===", market, code)
    cmd = [sys.executable, str(CAGENT / "scraper" / "cli.py"),
           "--stock", code, "--market", market,
           "--from", date_from, "--to", date_to,
           "--output", str(lakehouse)]
    if force:
        cmd.append("--force")
    run_step(cmd)


def step_ocr(market: str, code: str, lakehouse: Path, force: bool,
             latest: bool, pdf_limit: int) -> None:
    log.info("=== 阶段 2/3: vLLM OCR，PDF 转 markdown ===")
    cmd = [sys.executable, str(CAGENT / "ocr" / "cli.py"),
           "--stock", code, "--market", market,
           "--lakehouse", str(lakehouse)]
    if latest:
        cmd.append("--latest")
    if pdf_limit:
        cmd += ["--pdf-limit", str(pdf_limit)]
    if force:
        cmd.append("--force")
    run_step(cmd)


def step_report(market: str, code: str, template: Path,
                output: Path | None, trace: bool, extra: list[str]) -> None:
    log.info("=== 阶段 3/3: llama-server TOC + 报告生成 ===")
    cmd = [sys.executable, str(CAGENT / "report.py"),
           "--market", market, "--code", code,
           "--template", str(template)]
    if output:
        cmd += ["--output", str(output)]
    if trace:
        cmd.append("--trace")
    cmd += extra
    run_step(cmd)


def acquire_lock() -> Path:
    """全局互斥：pipeline 独占 GPU 与产出路径，并发跑会互相覆盖缓存与报告（实证踩过坑）。"""
    lock = SERVICE_STATE_DIR / "pipeline.lock"
    SERVICE_STATE_DIR.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return lock
        except FileExistsError:
            try:
                pid = int(lock.read_text().strip())
                os.kill(pid, 0)  # 进程还活着
            except (ValueError, ProcessLookupError):
                lock.unlink()  #  stale 锁，回收重试
                continue
            raise SystemExit(f"已有 pipeline 在运行（pid={pid}），等它结束或删掉 {lock}")
    raise SystemExit(f"无法获取锁 {lock}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="单公司端到端流水线：下载年报 → vLLM OCR → llama-server 报告生成")
    parser.add_argument("--market", choices=["cn", "hk"], required=True)
    parser.add_argument("--code", required=True, help="股票代码，如 09863")
    parser.add_argument("--template", type=Path,
                        default=Path("templates/template_business_model_single_company.md"))
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--trace", action="store_true",
                        help="记录每次 LLM 请求到 reports/.trace/agent-<时间戳>.jsonl")
    parser.add_argument("--from", dest="date_from", default="", help="起始日期 YYYY-MM-DD（默认：近5年）")
    parser.add_argument("--to", dest="date_to", default="", help="结束日期 YYYY-MM-DD（默认：今天）")
    parser.add_argument("--force", action="store_true", help="覆盖已有 PDF 并重新 OCR")
    parser.add_argument("--latest", action="store_true", help="OCR 每家公司只解析最新一份年报")
    parser.add_argument("--pdf-limit", type=int, default=0, help="每家公司最多解析 N 份 PDF，0=不限")
    parser.add_argument("--until", choices=["download", "ocr", "report"], default="report",
                        help="跑完指定环节后停止（默认 report=全链；例 --until ocr 表示"
                             "下载+OCR 后停，配合幂等下次不带 --until 续跑）")
    parser.add_argument("--lakehouse", default=str(DEFAULT_LAKEHOUSE),
                        help="lakehouse 根目录（默认 <仓库根>/.cagent）")
    parser.add_argument("--agent-arg", action="append", default=[],
                        help="透传给 cagent/agent.py 的额外参数（可多次指定）")
    args = parser.parse_args()

    lakehouse = Path(args.lakehouse)
    stages = [
        ("download", lambda: step_download(
            args.market, args.code, lakehouse,
            args.date_from, args.date_to, args.force)),
        ("ocr", lambda: step_ocr(
            args.market, args.code, lakehouse,
            args.force, args.latest, args.pdf_limit)),
        ("report", lambda: step_report(
            args.market, args.code, args.template,
            args.output, args.trace, args.agent_arg)),
    ]
    lock = acquire_lock()
    try:
        for name, run in stages:
            run()
            if name == args.until:
                log.info("按 --until %s 停止：后续环节未跑。重跑不带 --until 即可断点续跑"
                         "（已完成的环节幂等跳过）", args.until)
                break
    except KeyboardInterrupt:
        log.warning("收到中断（子进程已随之中断；重跑同一命令即可断点续跑）")
        sys.exit(130)
    except Exception as exc:
        log.error("流水线失败: %s", exc)
        sys.exit(1)
    finally:
        lock.unlink(missing_ok=True)
    if args.until == "report":
        log.info("流水线全部完成: %s %s", args.market, args.code)


main()
