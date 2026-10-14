"""finance 模块：财务与资本回报 writer

读取 finance_extract 产物（knowledge/*/financials.json），程序计算指标并拼多年序列表（表管数量）；
叙述段（资本回报 / 收入去向 / 现金流体检 / 异常信号 / 表外事项）由模型基于程序算好的数字撰写（文管叙述），
模型不碰数。口径遵循 docs/prd.md「资本回报的设计原则」：整体股东口径，不做每股折算。
产物 output/wiki/<market>/<stock>/finance.md。只产内容，不管 UI；页面渲染见 wiki_render.py。

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
from cagent.chunk import Toc
from cagent.finance_metrics import cash_check, expense_ratios, financial_income_share, income_split, load_financials, one_yuan_test, turnover, year_row
from cagent.wiki_render import render_doc

logger = logging.getLogger(__name__)

_YI = 1e8

_narrative_examples = [
    """
## 资本回报

整体股东口径下，2021-2025 年归母净利润从 67.3 亿增至 98.7 亿，归母净资产从 573.7 亿增至 878.9 亿，ROE 维持在 11%-13% 区间。三年窗口增量回报约 8%，低于存量 ROE，说明新投入资本的回报在递减。净现比五年均大于 1，利润有现金支撑；所有者收益受营运资本占用波动影响大，重资产折旧摊销每年超过 110 亿，维持性资本开支约 115 亿，利润中约一半以上是折旧回补的"纸面"部分。

## 收入去向（2025 年，每 100 元收入）

成本 82.0 元（供应商与一线员工），研发 4.7 元，销售 5.1 元，管理 2.1 元，税金及附加 3.8 元，财务费用为净收益 -0.9 元，投资与公允价值变动等损益 1.9 元，股东排最后分得 4.4 元。

## 现金流体检

经营现金流净流入占收入 18.1%，覆盖 13.1% 的投资净流出后仍有富余，筹资净流出 6.0% 主要为分红与还债，不靠外部输血。净现比 4.1，利润与现金同向，未见背离。

## 异常信号

2025 年财务费用为 -19.7 亿元（净收益），主因利息收入与汇兑，属非经营因素；财务性损益占净利润 12.3%，利润对非经营项目有一定依赖；应收款项融资较上年下降 41.7%，与收入增长的组合需关注渠道回款结构变化。其余成本费用率与周转率未见异常突变。
""",
]

_offbs_examples = [
    """
- 对外担保：为子公司银行授信提供担保余额 23.5 亿元，均在合并范围内。
- 重大承诺：在建产能资本承诺 18.2 亿元。
""",
]


def _fmt_yi(v: float | None) -> str:
    return "—" if v is None else f"{v / _YI:.1f}"


def _fmt_ratio(v: float | None, pct: bool = False) -> str:
    if v is None:
        return "—"
    return f"{v * 100:.1f}%" if pct else f"{v:.2f}"


def _series_table(series: dict, years: list[int]) -> str:
    """多年指标序列表（亿元；比率列无量纲）。"""
    header = "| 年份 | 收入 | 归母净利润 | 归母净资产 | 经营现金流 | 资本开支 | 折旧摊销 | 分红 | 所有者收益 | ROE | 三年增量回报 | 净现比 |\n| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"
    rows = []
    for y in years:
        r = year_row(series, y)
        rows.append(
            f"| {y} | {_fmt_yi(r['收入'])} | {_fmt_yi(r['归母净利润'])} | {_fmt_yi(r['归母净资产'])} | {_fmt_yi(r['经营现金流'])} "
            f"| {_fmt_yi(r['资本开支'])} | {_fmt_yi(r['折旧摊销'])} | {_fmt_yi(r['分红'])} | {_fmt_yi(r['所有者收益'])} "
            f"| {_fmt_ratio(r['ROE'], pct=True)} | {_fmt_ratio(r['三年增量回报'], pct=True)} | {_fmt_ratio(r['净现比'])} |"
        )
    return header + "\n" + "\n".join(rows) + "\n"


def _metrics_context(series: dict, years: list[int]) -> str:
    """喂给模型的指标文本：逐年摘要（含成本费用率）+ 最新年收入去向 + 一元检验。"""
    lines = []
    for y in years:
        fin, prev = series[y], series.get(y - 1)
        r = year_row(series, y)
        t = turnover(fin, prev)
        c = cash_check(fin)
        er = expense_ratios(fin)
        fs = financial_income_share(fin)
        lines.append(
            f"{y}: 收入{_fmt_yi(r['收入'])}亿 归母净利{_fmt_yi(r['归母净利润'])}亿 归母净资产{_fmt_yi(r['归母净资产'])}亿 "
            f"经营现金流{_fmt_yi(r['经营现金流'])}亿 资本开支{_fmt_yi(r['资本开支'])}亿 折旧摊销{_fmt_yi(r['折旧摊销'])}亿 "
            f"分红{_fmt_yi(r['分红'])}亿 所有者收益{_fmt_yi(r['所有者收益'])}亿 ROE {_fmt_ratio(r['ROE'], pct=True)} "
            f"三年增量回报 {_fmt_ratio(r['三年增量回报'], pct=True)} 净现比 {_fmt_ratio(r['净现比'])} "
            f"成本率 {_fmt_ratio(er['成本率'], pct=True)} 销/管/研/财费用率 "
            f"{_fmt_ratio(er['销售费用率'], pct=True)}/{_fmt_ratio(er['管理费用率'], pct=True)}/"
            f"{_fmt_ratio(er['研发费用率'], pct=True)}/{_fmt_ratio(er['财务费用率'], pct=True)} "
            f"财务性损益/归母净利 {_fmt_ratio(fs, pct=True)} "
            f"应收周转 {_fmt_ratio(t['应收账款周转率'])} 存货周转 {_fmt_ratio(t['存货周转率'])} 总资产周转 {_fmt_ratio(t['总资产周转率'])} "
            f"经营净额/收入 {_fmt_ratio(c['经营净额/收入'], pct=True)} 投资净额/收入 {_fmt_ratio(c['投资净额/收入'], pct=True)} "
            f"筹资净额/收入 {_fmt_ratio(c['筹资净额/收入'], pct=True)}"
        )
    latest = years[-1]
    oy = one_yuan_test(series, latest)
    split = income_split(series[latest])
    split_lines = "\n".join(f"{k} {v:.1f}%" for k, v in split.items() if v is not None)
    return (
        f"一元检验（近3年每留存1元利润变成的归母净资产）: {_fmt_ratio(oy)}\n"
        + "\n".join(lines)
        + f"\n\n最新一年（{latest}）收入去向（每100元收入）:\n{split_lines}"
    )


def _narrative_prompt(metrics: str) -> str:
    return textwrap.dedent(f"""
        <requirement>
        你在为价值投资者撰写公司财务分析的叙述部分。所有数字已由程序计算好并给在 <metrics> 中，
        禁止编造、改动、四舍五入任何数字，只使用给定数字；「—」表示该项缺数据，不要讨论。
        简体中文，markdown，输出且只输出以下四个二级标题段落，每段 250 字以内：

        ## 资本回报 —— 整体股东口径（归母）描述这门生意为资本工作的能力：ROE 水平与多年趋势、
        三年增量回报（再投一元赚多少，与存量 ROE 的差说明什么）、一元检验（留存利润变成了多少归母净资产）、
        所有者收益与净利润的差异及原因。注意：三年增量回报与一元检验的分母是归母净资产变动，
        含增发、回购与其他综合收益的影响；若归母净资产大幅增长而利润未同步，先考虑增发或重估因素，
        不要直接下"回报递减"的结论。不提每股数字，不下投资结论。
        ## 收入去向 —— 用 <metrics> 中最新一年收入去向数据，写每 100 元收入被谁分走、股东排最后分到什么；
        其他损益、营业外收支、财务性损益占比高时，指出利润对非经营项目的依赖。
        ## 现金流体检 —— 经营/投资/筹资格局（占收入比），净现比是否背离，是否靠筹资输血维持。
        ## 异常信号 —— 纵向对比多年序列找突变：成本率与各项费用率突变、周转率突变、利润与现金流背离、
        财务性损益占比异常，逐项给可能的业务解释；没有异常就明确写"未见明显异常"。
        </requirement>

        <examples>
            {"\n".join(f"<example>{e}</example>" for e in _narrative_examples)}
        </examples>

        <metrics>
        {metrics}
        </metrics>""").strip()


def _offbs_prompt() -> str:
    return textwrap.dedent(f"""
        <requirement>
        你在阅读上市公司年报附注，提取报表上看不见的义务与风险（隐形负债）：
        对外担保、未决诉讼、关联方资金占用、重大资本承诺、环保治理义务。
        只写有明确数字或事实的条目，每条一行，格式"- 事项：金额/事实"，简体中文，300 字以内。
        没有就写"未见重大表外事项"。
        </requirement>

        <examples>
            {"\n".join(f"<example>{e}</example>" for e in _offbs_examples)}
        </examples>""").strip()


def _gen_off_balance(agent: Agent, series: dict, root: Path, market: str, stock: str) -> str:
    """从最新年报附注提取表外事项；提取失败不阻断主流程。"""
    fin = series[max(series)]
    md_path = Path(root) / market / stock / "derived" / fin["source"]["file"]
    try:
        toc = Toc(agent, md_path)
        picked = toc.pick_chapters(toc.chapters, "找出或有事项、承诺、担保、诉讼、关联方往来、资产负债表日后事项相关章节")
        chunks = [chunk for i in picked for chunk in toc.chapter_text_chunk(i)]
        return agent.chat_reduce_chunks(_offbs_prompt(), chunks).strip()
    except Exception:
        logger.exception("表外事项提取失败，跳过该段")
        return ""


def gen_finance(agent: Agent, stock: str, market: str, root: Path) -> str:
    series = load_financials(root, market, stock)
    years = sorted(series)[-5:]
    logger.info(f"年份: {years}")

    table = _series_table(series, years)
    metrics = _metrics_context(series, years)
    narrative = agent.chat_prompt(_narrative_prompt(metrics)).strip()
    if not narrative:
        raise ValueError(f"{stock} {market} finance 叙述异常")

    offbs = _gen_off_balance(agent, series, root, market, stock)

    doc = "# 财务与资本回报\n\n## 五年序列（亿元）\n\n" + table + "\n" + narrative + "\n"
    if offbs:
        doc += "\n## 表外事项\n\n" + offbs + "\n"
    return doc


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S", stream=sys.stderr)
    parser = argparse.ArgumentParser(description="finance 模块：财务与资本回报")
    parser.add_argument("--stock", default="601633")
    parser.add_argument("--market", default="cn", choices=["cn", "hk"])
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1] / ".cagent"))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--render", action="store_true", help="生成后渲染 finance.html")
    args = parser.parse_args()

    output_dir = Path(__file__).resolve().parents[1] / "output" / "wiki" / f"{args.market}" / f"{args.stock}"
    output_dir.mkdir(parents=True, exist_ok=True)

    md_output_path = output_dir / "finance.md"
    if md_output_path.exists() and not args.force:
        doc = md_output_path.read_text(encoding="utf-8")
    else:
        root = Path(args.root)
        agent = Agent(trace=True)
        doc = gen_finance(agent, args.stock, args.market, root)
        md_output_path.write_text(doc, encoding="utf-8")

    print(doc)

    if args.render:
        html_output_path = output_dir / "finance.html"
        html_output_path.write_text(render_doc([doc], title=f"{args.stock} · 财务与资本回报", ticker=args.stock), encoding="utf-8")
        logger.info(f"渲染: {html_output_path}")


if __name__ == "__main__":
    main()
