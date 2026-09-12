"""本地推理服务的管理：启动、探活、进程组回收。

入口脚本（report.py 的 ensure_service 自管）与 tests（环节验证自管服务）共用
同一套服务生命周期，这是唯一定义处。零第三方依赖；服务启动用的模型路径等
常量也在这里。

约定：run_service(spec, work) 启动服务 → 就绪后执行 work() → 结束/异常时按进程组
终止服务。已经在跑的服务不在本模块的管辖内——调用方（如 tests/_util.with_llm）
负责先探活，探活不到才来这里拉一个。

服务按模型功能划分（对应 README「依赖模型」），不按推理后端（vllm/llama）划分：
- TEXT：文本生成（标注、摘要、叙事、装配、对账），llama-server + Qwen3.6-35B，
  OpenAI 兼容接口，端口 9931
- OCR：PDF 页面 → markdown，vllm serve + OvisOCR2（端到端，不做版面检测），
  OpenAI 兼容接口，端口 8000。启动命令见 README「依赖模型」
"""

import logging
import os
import shlex
import shutil
import signal
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import NamedTuple

log = logging.getLogger(__name__)

SERVICE_LOG_DIR = Path(__file__).resolve().parent.parent.parent / "output" / ".services"


class Service(NamedTuple):
    """一个本地推理服务的启动规格：按模型功能命名（TEXT/OCR），而非按后端引擎。"""

    name: str
    cmd: str
    health_url: str
    ready_timeout: int
    env: dict[str, str]


TEXT = Service(
    name="text",
    cmd=(
        "llama-server "
        "-m ~/.cache/modelscope/models/unsloth--Qwen3.6-35B-A3B-MTP-GGUF/snapshots/master/Qwen3.6-35B-A3B-UD-IQ3_S.gguf "
        "--spec-type draft-mtp --spec-draft-n-max 2 "
        "-c 65536 -ngl 99 -ncmoe 28 -fa on "
        "-ctk q4_0 -ctv q4_0 "
        "-b 2048 -ub 2048 "
        "-t 8 -np 1 --fit off --port 9931 --reasoning off"
    ),
    health_url="http://127.0.0.1:9931/health",
    ready_timeout=1800,
    env={},
)

# OCR 服务：vllm serve + OvisOCR2（端到端，不做版面检测），命令与 README 一致
OCR = Service(
    name="ocr",
    cmd=(
        "vllm serve ~/.cache/modelscope/models/ATH-MaaS--OvisOCR2/snapshots/master/ "
        "--trust-remote-code "
        "--served-model-name OvisOCR2 "
        "--mm-processor-cache-gb 0 "
        "--max-model-len 32768 "
        "--gpu-memory-utilization 0.70"
    ),
    health_url="http://localhost:8000/health",
    ready_timeout=1800,
    # WSL2 下 vLLM 默认禁用 pinned memory（进而 UVA 不可用、多模态缓冲区初始化失败），
    # 本机内核 6.18 支持 pinned memory，需显式打开。仅作用于 vLLM 启动环境。
    env={"VLLM_WSL2_ENABLE_PIN_MEMORY": "1"},
)

# 本机服务不走环境代理：urllib 默认读 http_proxy，回环请求会被发给代理并拿到 502
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def wait_ready(name: str, proc: subprocess.Popen, url: str, timeout: int,
               log_path: Path) -> None:
    """轮询服务健康检查接口，就绪返回；进程提前退出或超时则报错。"""
    deadline = time.monotonic() + timeout
    last = "未探测"
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"{name} 进程已退出（code={proc.returncode}），见 {log_path}")
        try:
            with _NO_PROXY_OPENER.open(url, timeout=5) as resp:
                if resp.status == 200:
                    log.info("%s 已就绪 (%s)", name, url)
                    return
                last = f"HTTP {resp.status}"
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
        log.debug("%s 未就绪，继续等待（%s）", name, last)
        time.sleep(2)
    raise TimeoutError(f"{name} 等待就绪超时（{timeout}s，{url}）；"
                       f"最后一次探测 {last}；见 {log_path}")


def _terminate_group(proc: subprocess.Popen, timeout: int = 30) -> None:
    """按进程组终止（vllm/llama-server 会 fork worker），SIGTERM 不退就升级 SIGKILL。

    leader 已退不等于组内干净：仍要 killpg 一次，否则 worker 占着显存，下一轮启动
    只能干等超时。SIGKILL 也只杀主 pid 的话同样漏掉 worker。
    """
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=timeout)
            return
        except subprocess.TimeoutExpired:
            log.warning("pid=%d 未在 %ds 内退出，升级信号", proc.pid, timeout)
    log.error("pid=%d 连 SIGKILL 都没退（多半卡在内核态），显存可能仍被占用", proc.pid)


def probe(url: str) -> bool:
    """健康探测：HTTP 200 视为服务可用。本机服务不走环境代理。"""
    try:
        with _NO_PROXY_OPENER.open(url, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def ensure_service(spec: Service, work) -> None:
    """保证服务可用后执行 work：已有在跑的直接复用（不归本进程管，也不关闭）；
    没有的按 spec 拉起一个，work 结束/异常时确保关闭（语义同 run_service）。"""
    if probe(spec.health_url):
        log.info("%s 已在跑（%s），直接复用", spec.name, spec.health_url)
        work()
        return
    run_service(spec, work)


def run_service(spec: Service, work) -> None:
    """启动一个本地推理服务，就绪后执行 work()，结束/异常时确保服务被关闭。"""
    argv = [os.path.expanduser(t) for t in shlex.split(spec.cmd)]  # 无 shell，需自行展开 ~
    exe = argv[0]
    if shutil.which(exe) is None:
        raise RuntimeError(f"找不到 {exe}，无法启动 {spec.name}")
    SERVICE_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = SERVICE_LOG_DIR / f"{spec.name}.log"
    child_env = {**os.environ, **spec.env} if spec.env else None
    with open(log_path, "ab") as log_file:
        log.info("启动 %s: %s（日志: %s）", spec.name, spec.cmd, log_path)
        proc = subprocess.Popen(argv, stdout=log_file, stderr=subprocess.STDOUT,
                                start_new_session=True, env=child_env)
        try:
            wait_ready(spec.name, proc, spec.health_url, spec.ready_timeout, log_path)
            work()
        finally:
            log.info("关闭 %s (pid=%d)", spec.name, proc.pid)
            _terminate_group(proc)
