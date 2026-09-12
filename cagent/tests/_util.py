"""环节验证共享装配：LLM 连接、单份公告定位、检查结果打印。

无缓存：每个测试每次全量重算，进程内组合上游环节（不读磁盘中间产物）。
cagent 是可编辑安装的项目包（uv sync 装进 venv），一律绝对导入（`cagent.*`），
不注入 sys.path。llm 服务自管：已在跑的复用，没有的按 lib 同款命令拉起、跑完即关
（见 with_llm）。
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

from cagent.lib.lakehouse import LakehouseReader
from cagent.llm import try_connect
from cagent.pageindex.reader import PAGEINDEX_SUFFIX

log = logging.getLogger("cagent-test")


def base_parser(desc: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=desc)
    p.add_argument("--market", default="cn")
    p.add_argument("--code", default="601633")
    p.add_argument("--file", default=None,
                   help="只测 derived/ 下指定公告文件名；默认取最新一份公告")
    p.add_argument("--trace", action="store_true")
    return p


def connect(trace: bool, name: str):
    trace_path = None
    if trace:
        trace_path = Path("reports/.trace") / f"test-{name}-{datetime.now():%Y%m%d-%H%M%S}.jsonl"
    lm = try_connect(trace_path=trace_path)
    if lm is None:
        raise SystemExit("连接失败（服务未起，且 with_llm 自拉起也不可用——看上面的错误日志）")
    return lm


def with_llm(work, ready_timeout: int = 1800) -> None:
    """保证环节验证有 llama-server 可用后执行 work（probe 到在跑的直接复用，
    没有的按 lib 服务层同款命令拉起，work 跑完即关）。拉起是重操作（等模型加载、
    占满 GPU），只有连不上时才发生。"""
    from cagent.lib.service import LLAMA_CMD, LLAMA_HEALTH_URL, ensure_service
    ensure_service("llama-server", LLAMA_CMD, LLAMA_HEALTH_URL, ready_timeout, work)


def with_vllm(work, ready_timeout: int = 1800) -> None:
    """保证 OCR 环节验证有 vllm（8000）可用后执行 work；规则同 with_llm。"""
    from cagent.lib.service import VLLM_CMD, VLLM_HEALTH_URL, ensure_service
    ensure_service("vllm", VLLM_CMD, VLLM_HEALTH_URL, ready_timeout, work)


def pick_filing(reader: LakehouseReader, market: str, code: str,
                file: str | None) -> tuple[dict, Path]:
    """定位单份公告，返回 (filing meta, derived 目录)。默认取最新一份有 TOC 的年报
    （通知信函等无目录文件本来就不该进测试；.pageindex.json 缺失时自动顺延下一份）。"""
    data_dir = reader.company_dir(market, code)
    derived = data_dir / "derived"
    filings = reader.list_filings(data_dir)
    if not filings:
        raise SystemExit(f"{derived} 下没有公告")
    if file:
        hit = [f for f in filings if f["file"] == file]
        if not hit:
            raise SystemExit(f"{derived} 下找不到 {file}")
        return hit[0], derived
    with_toc = [f for f in filings
                if (derived / f["file"]).with_suffix(PAGEINDEX_SUFFIX).exists()]
    return (with_toc or filings)[-1], derived  # list_filings 为时间序，取最新


def report(checks: list[tuple[str, bool, str]]) -> None:
    """打印 (名称, 是否通过, 细节) 检查清单；有失败则退出码 1。"""
    bad = 0
    for name, ok, detail in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")
        bad += not ok
    print(f"\n{len(checks) - bad}/{len(checks)} 项通过")
    if bad:
        sys.exit(1)
