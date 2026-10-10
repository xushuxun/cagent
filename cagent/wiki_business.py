"""business 模块：生意过程 writer

通读指定年份年报（默认最新），蒸馏这门生意如何运转，产物为 Markdown 文档
knowledge/fy<year>/business.json。只产内容，不管 UI；页面渲染见 wiki_render.py。

用法：
    uv run cagent/wiki_business.py --stock 601633 --market cn
    uv run cagent/wiki_business.py --stock 601633 --market cn --force
    uv run cagent/wiki_business.py --stock 601633 --market cn --render
"""

import argparse
import json
import logging
import sys
import textwrap
from pathlib import Path

from cagent.agent import Agent
from cagent.chunk import Toc, report_year
from cagent.wiki_render import render_doc

logger = logging.getLogger(__name__)


_intro_examples = [
    """### 业务说明

沐曦股份是一家 GPU 芯片设计公司，只做研发设计和销售，晶圆制造、封装测试全部委外；主要产品为训推一体、推理和图形渲染 GPU，以板卡及集成板卡的服务器形式交付。

公司向上游采购晶圆并委托封装测试和板卡加工，对关键物料预付货款、战略备货；产品以直销与经销结合的方式销售，部分客户在签订合同时预付一定比例货款。

公司客户为服务器厂商、系统集成商和智算中心建设方，前五大客户占年度销售额 61%；收入几乎全部来自中国内地。

资金流方面，公司回款慢于付款：客户按信用期回款，部分客户待收到最终用户款项后才与公司结算（背靠背），而上游采购需大额预付。2025 年经营活动现金流量净额为 -12.60 亿元，缺口由股权融资弥补（当年 IPO 募资净额 39 亿元）。公司累计未弥补亏损尚未填平，本年度不分红。

### 经营过程

```mermaid
flowchart LR
    A["研发<br>GPU 芯片设计"] --> B["采购<br>晶圆与 HBM 预付锁货"] --> C["生产<br>流片封测与板卡加工委外"] --> D["销售<br>直销与经销结合"] --> E["交付<br>板卡与服务器整机"] --> F["回款<br>信用期内回款，部分背靠背"]
```
""",


    """### 业务说明

MiniMax 是一家全球化的大模型公司，自研全模态模型，收入来自两处：C 端 AI 应用订阅（占 67%）和 B 端开放平台 API 调用与企业服务（占 33%）。

公司轻资产运营，向云厂商租用算力、不持有硬件；C 端订阅需预付且不可退，B 端 API 按月按调用量结算。

公司服务超 2.3 亿个人用户和 21 万企业客户及开发者，客户分散（前五大占 19%），约七成收入来自中国内地以外。

资金流方面，公司收款先于付款，但经营现金流仍为负：2025 年经营活动现金净流出 2.80 亿美元，缺口由发行优先股弥补（当年募资 4.26 亿美元，已于 2026 年 1 月上市时转为普通股）。公司未派息，账面现金及理财约 10.5 亿美元。

### 经营过程

```mermaid
flowchart LR
    A["研发<br>全模态模型训练"] --> B["采购<br>租用云厂商算力"] --> C["产品化<br>应用与开放平台"] --> D["销售<br>C 端订阅，B 端按量"] --> E["交付<br>云端调用"] --> F["回款<br>订阅预付，企业月结"]
```
""",
]



def _load_latest_annual(agent: Agent, stock: str, market: str, root: Path) -> tuple[int, Path, Toc, list[int], list[str]]:
    """加载最新一份年报，选择相关章节。返回 (year, md_path, toc, picked, chunks)。"""
    mds = sorted((root / market / stock / "derived").glob("*.md"))
    if not mds:
        raise SystemExit(f"{root / market / stock / 'derived'} 没有年报 md，先跑 ocr/cli.py")
    md_path = mds[-1]

    toc = Toc(agent, md_path)
    logger.info(f"阅读: {md_path.name}")

    pick_task = "你在通读一份上市公司年报的目录。大致了解企业商业过程，不分析财务，需要阅读哪些章节？"
    picked = toc.pick_chapters(toc.chapters, pick_task)

    logger.info(f"选中章节: {[toc.chapters[i]['title'] for i in picked]}")
    chunks = [chunk for i in picked for chunk in toc.chapter_text_chunk(i)]
    return report_year(agent, md_path), md_path, toc, picked, chunks


def gen_business(agent: Agent, stock: str, market: str, root: Path) -> tuple[int, str]:
    # 商业模式分析只需要最新一份年报
    year, _md_path, toc, picked, chunks = _load_latest_annual(agent, stock, market, root)

    logger.info("gen …")

    prompt = textwrap.dedent(f"""
        <requirement>
        你在阅读一份上市公司年报，要把这门生意的运行过程讲清楚

        - 只描述生意如何运转，禁止分析财务数据、评价竞争力；不输出页码和章节名
        - 只写原文有依据的内容，禁止编造原文不存在的细节
        - 全文控制在1000字以内，语言用简体中文
        </requirement>

        <examples>
        {"\n".join(f"<example>{e}</example>" for e in _intro_examples)}
        </examples>""").strip()

    intro = agent.chat_reduce_chunks(prompt, chunks)

    if not intro.strip() :
        raise ValueError(f"{year} business 分析异常")

    return year, intro


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S", stream=sys.stderr)
    parser = argparse.ArgumentParser(description="business 模块：蒸馏生意过程，产物 knowledge/fy<year>/business.json")
    parser.add_argument("--stock", default="601633")
    parser.add_argument("--market", default="cn", choices=["cn", "hk"])
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1] / ".cagent"))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--render", action="store_true", help="生成后渲染 business.html")
    args = parser.parse_args()

    root = Path(args.root)
    agent = Agent(trace=True)
    _year, doc = gen_business(agent, args.stock, args.market, root)

    print(doc)

    if args.render:
        wiki_html_output = root / "output" / "wiki" / f"{args.stock}"
        render_doc(doc)


if __name__ == "__main__":
    main()
