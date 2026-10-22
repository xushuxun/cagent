"""生意模式：把年报里的生意讲清楚

读最新年报的公司介绍、行业情况、管理层讨论、风险因素等章节，分两小节：
  - 生意是什么：客户、产品、钱从哪来、成本花在哪、流程与上下游；
  - 行业进入门槛：牌照、投入与周期、关键资源、获客方式、规模效应、监管约束，
    护城河从"挡住新进入者的东西"里现形。
只写年报披露的内容；不写财务数字，不算账，不标页码。

    uv run -m cagent.wiki.business --stock 601633 --market cn
"""

import argparse
import logging
import sys
import textwrap
from pathlib import Path

from cagent.core.agent import Agent
from cagent.core.chunk import Toc

logger = logging.getLogger(__name__)

# _examples = []

def _get_latest_annual(stock: str, market: str, root: Path) -> Path:
    derived = root / market / stock / "derived"
    mds = sorted(derived.glob("*.md"))
    if not mds:
        raise SystemExit(f"{derived} 没有年报 md，先跑 cagent.ocr.cli")

    # 生意模式分析，只需要最新一份年报；文件名前缀是公告日期，排序后取最后一个
    return mds[-1]


def _gen(agent: Agent, stock: str, market: str, root: Path) -> str:
    md_path = _get_latest_annual(stock, market, root)
    logger.info(f"select {md_path}")
    toc = Toc(agent, md_path)
    chunks = toc.get_chapters_chunks(
        task="为了搞清楚企业生意模式，找出相关章节，除了财务报表章节，都尽量召回"
    )
    # 两阶段生成：先写"生意是什么"，再增量写"从零开始"。
    # 两次调用 chunk 前置、共享前缀，KV 复用交给引擎的公共前缀匹配。
    prompt_what = textwrap.dedent(
        """
        <requirement>
        你在阅读一份上市公司年报，只说明生意是怎么做的。
        讲清：
          - 客户是谁、产品/服务是什么、钱从哪来、成本花在哪；
          - 研发、采购、生产、销售、交付的详细流程；
          - 价值链上的位置、与上下游的关系；
          - 区域、渠道与客户结构。
        按年报披露的内容写；不要写财务金额、比率或具体财务数字；不要标页码、不写出处。全文控制在 600 字以内。

        格式与话术：
        - 只写原文有依据的内容，禁止编造原文不存在的细节；去掉管理层吹嘘话术，语言简洁凝炼
        - 用简体中文，markdown 格式
        </requirement>
        """
    ).strip()
    prompt_entry = textwrap.dedent(
        """
        <requirement>
        你在阅读一份上市公司年报。<business-model> 是对这家公司生意的概括，<chunk> 是年报原文。
        分析总结"如何从零开始做这门生意", 强调这门生意的建立过程，如何做，不判断护城河强弱
        
        格式与话术：
        - 强调这是AI生成的，不代表公司历史，语言简洁凝炼
        - 用简体中文，markdown 格式
        </requirement>
        """
    ).strip()
    what = agent.chat_distill(prompt_what, chunks)
    entry = agent.chat_distill(f"<business-model>\n{what}\n</business-model>\n\n{prompt_entry}", chunks)
    if not what or not entry:
        raise ValueError("生意模式生成异常：空输出")
    return f"# 生意模式\n\n{what}\n\n## 这门生意的建立过程\n\n{entry}\n"


def build(agent: Agent, stock: str, market: str, root: Path, output_dir: Path, force: bool = False) -> str:
    """写生意模式；产物在就复用。返回整节 markdown（含一级标题）。"""
    target = output_dir / "business.md"
    if target.exists() and not force:
        logger.info(f"reuse {target.name}")
        return target.read_text(encoding="utf-8")
    doc = _gen(agent, stock, market, root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(doc, encoding="utf-8")
    logger.info(f"write {target}")
    return doc


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S", stream=sys.stderr)
    parser = argparse.ArgumentParser(description="生意模式")
    parser.add_argument("--stock", default="601633")
    parser.add_argument("--market", default="cn", choices=["cn", "hk"])
    parser.add_argument("--root", type=Path, default=Path(".cagent"))
    parser.add_argument("--out", type=Path, default=Path("output/wiki"))
    parser.add_argument("--force", action="store_true", help="重跑（默认复用已有小节）")
    args = parser.parse_args()

    llm = Agent(trace=True)
    build(llm, args.stock, args.market, args.root, args.out / args.market / args.stock, force=args.force)


if __name__ == "__main__":
    main()
