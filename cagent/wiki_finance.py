"""finance writer 模块：资本回报拆解

目标只有一个：把这家公司过去真实的资本回报拆开看清楚——投进去多少资本、赚回多少真钱、
分红与留存怎么分、留下的钱又赚回了多少、回报是不是靠杠杆或非经营损益撑起来的。

证据只有一个来源：finance_series 给的证据表（多年序列、利润表科目、同比与自由现金流、
经营活动现金流量调节表、每 100 元收入去向、现金流结构、资本与负债结构、成本费用率与周转率、
累计与滚动），每个数都由程序直解、可溯源到年报页码，模型只许照抄，不许计算。

产物 output/wiki/<market>/<stock>/finance.md。只拆解事实，不下投资结论。

用法：
    uv run cagent/wiki_finance.py --stock 601633 --market cn
    uv run cagent/wiki_finance.py --stock 601633 --market cn --force
    uv run cagent/wiki_finance.py --stock 601633 --market cn --render
"""

import argparse
import logging
import sys
import textwrap
from pathlib import Path

from cagent.agent import Agent
from cagent.finance_series import evidence, load_series
from cagent.wiki_render import render_doc

logger = logging.getLogger(__name__)

# 拆解维度：照 prd.md「资本回报的设计原则」的链条走，缺一段这条链就断
_DIMENSIONS = """### 1. 投入了什么资本
累计股东投入与留存利润各有多少（看「累计与滚动」表）；这家公司的扩张靠的是股东追加投入还是自身留存。

### 2. 赚回了多少真钱
所有者收益逐年是多少、与归母净利润差在哪里（看「多年序列」）；折旧摊销与资本开支的相对大小说明什么。

### 3. 分红与留存怎么分
分红金额、分红占归母净利的比例、累计分红（看「多年序列」与「累计与滚动」）；留下来的钱有多少。

### 4. 留下的钱赚回了多少
增量资本回报与一元检验逐年变化（看「累计与滚动」），与 ROE 对照看：新投的资本比存量资本赚得多还是少。

### 5. 回报是怎么来的
收入去向的变化（看「利润表科目」「每 100 元收入去向」「同比变化与自由现金流」）：
利润率、费用率、周转率各起了什么作用；哪些损益是经营性的、哪些靠天吃饭。

### 6. 现金是否支撑得住
按「经营活动现金流量调节表」逐步说明净利润与经营现金流的差额由哪些项目构成、哪些年份对不上；
再看「现金流结构」里经营、投资、筹资三项的组合。

### 7. 是不是靠杠杆撑起来的
有息负债、净现金、有息负债与归母净资产的比（看「资本与负债结构」）；剔除杠杆后回报还剩多少。"""

def _output_dir(market: str, stock: str) -> Path:
    return Path(__file__).resolve().parents[1] / "output" / "wiki" / market / stock


def _demote(markdown: str, levels: int = 1) -> str:
    """把整段 markdown 的标题降级，便于嵌进上层文档。"""
    return "\n".join(("#" * levels + line if line.startswith("#") else line) for line in markdown.strip().splitlines())


def _business_context(market: str, stock: str) -> str:
    path = _output_dir(market, stock) / "business.md"
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


def decompose_capital_return(agent: Agent, evidence_text: str, business_text: str) -> str:
    """按固定维度拆解资本回报：所有数字只能来自证据表。"""
    prompt = textwrap.dedent(f"""
        <requirement>
        拆解这家公司过去真实的资本回报。只依据 <evidence> 里的数字，只写 <business> 与证据表支撑得了的内容。

        - 只许引用证据表中出现过的数字，不许引入外部数字、不许估算、不许外推
        - 不许自行计算：同比、占比、差额、倍数一律照抄证据表；表里没有就不写
        - 不要用比率反推金额、也不要用金额反推比率
        - 每个数字后面注明取自哪张表（如「多年序列」「累计与滚动」「经营活动现金流量调节表」）
        - 严格按下面的维度写，小标题照用，每条 2 到 4 句，能写多实就写多实，数据不够就写「证据表未提供」
        - 只拆解已经发生的事实，不预测未来、不评价贵贱、不给买卖建议
        - 除下面的小标题外不要再输出其他标题
        </requirement>

        <dimensions>
        {_DIMENSIONS}
        </dimensions>

        <business>
        {business_text or "（缺生意模式文档）"}
        </business>

        <evidence>
        {evidence_text}
        </evidence>""").strip()
    return agent.chat_prompt(prompt).strip()


def gen_finance(agent: Agent, stock: str, market: str, root: Path) -> str:
    series = load_series(root, market, stock)
    years = sorted(series)[-5:]
    evidence_text = "\n".join(evidence(series, years).splitlines()[1:]).strip()  # 去掉证据表自带的一级标题，嵌入时不再多一层
    logger.info(f"证据表就绪：{years[0]}–{years[-1]}，{len(evidence_text)} 字符")

    logger.info("拆解资本回报 …")
    decomposition = decompose_capital_return(agent, evidence_text, _business_context(market, stock))
    if not decomposition:
        raise ValueError(f"{stock} {market} 资本回报拆解异常")

    return f"# 财务分析 · 资本回报拆解\n\n## 一、数据\n\n{_demote(evidence_text)}\n\n## 二、资本回报拆解\n\n{decomposition}\n"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S", stream=sys.stderr)
    parser = argparse.ArgumentParser(description="finance 模块：资本回报拆解")
    parser.add_argument("--stock", default="601633")
    parser.add_argument("--market", default="cn", choices=["cn", "hk"])
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1] / ".cagent"))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--render", action="store_true", help="生成后渲染 finance.html")
    args = parser.parse_args()

    output_dir = _output_dir(args.market, args.stock)
    output_dir.mkdir(parents=True, exist_ok=True)

    md_output_path = output_dir / "finance.md"
    if md_output_path.exists() and not args.force:
        doc = md_output_path.read_text(encoding="utf-8")
    else:
        doc = gen_finance(Agent(trace=True), args.stock, args.market, Path(args.root))
        md_output_path.write_text(doc, encoding="utf-8")

    print(doc)

    if args.render:
        html_output_path = output_dir / "finance.html"
        html_output_path.write_text(render_doc([doc], title=f"{args.stock} · 资本回报拆解", ticker=args.stock), encoding="utf-8")
        logger.info(f"渲染: {html_output_path}")


if __name__ == "__main__":
    main()
