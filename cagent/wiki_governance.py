"""governance 模块：治理与激励 writer

通读指定年份年报（默认最新），蒸馏价值投资关心的"人"与制度：
控制权与利益绑定、薪酬与激励、分红与股东回报、关联体系。
不罗列委员会名单与成员，不做员工构成表。产物落位 knowledge/fy<year>/governance.json。

用法：
    uv run cagent/wiki_governance.py --stock 601633 --market cn
    uv run cagent/wiki_governance.py --stock 601633 --market cn --force
"""

import argparse
import json
import logging
import sys
import textwrap
from pathlib import Path

from cagent.agent import Agent
from cagent.chunk import Toc, report_year

logger = logging.getLogger(__name__)


_governance_examples = [
    {
        "pages": """董事会目前由九名董事组成，其中包括四名执行董事，即闵俊杰博士、贫炜祎女士、赵鹏宇先生及周彧聪先生；两名非执行董事，即陈英杰先生及刘伟先生；以及三名独立非执行董事，即黄国滨先生、王鹏程博士及朱华星博士。闵俊杰博士担任主席。本公司没有区分主席及首席执行官，闵俊杰博士目前兼任这两个职务。

2025年执行董事薪酬（合计薪酬，千美元）：贫炜祎女士3,223；闵俊杰博士241；赵鹏宇先生896；周彧聪先生1,318，合计5,678千美元。年内概无向非执行董事支付酬金。

首次公开发售前股份激励计划由本公司于2022年采纳，根据该计划授出的未行使购股权的相关A类普通股一直由雇员持股平台持有，上市后悉数归属不会对股东股权产生任何摊薄影响。本公司已向392名承授人授出购股权认购合共20,890,736股A类普通股。首次公开发售后股份激励计划自无条件之日起10年内有效，剩余年期约九年零八个月。

由于本公司的股份在报告期内未在联交所上市，董事出席股东大会的记录将在本公司后续年度报告中披露。于截至2025年12月31日止年度，概无订立或存续任何股权挂钩协议，本公司并无购买、出售或赎回任何上市证券。

与关连方订立持续关连交易：主API服务协议、阿里巴巴云端服务协议及业务合作协议。""",
        "output": """### 控制权与利益绑定
<table>
<tr><th>问题</th><th>事实</th><th>含义</th></tr>
<tr><td>谁控制公司</td><td class="q">采用不同投票权架构；闵俊杰任董事会主席，主席与首席执行官不分离</td><td class="q">控制权与经营权集中于创始人</td></tr>
<tr><td>管理层从哪来</td><td class="q">四名执行董事分掌整体战略、运营、语言模型与视觉模型研发</td><td class="q">核心管理层为创始团队</td></tr>
<tr><td>决策是否制衡</td><td class="q">董事会九人，含三名独立非执行董事</td><td class="q">—</td></tr>
</table>

### 薪酬与激励
<table>
<tr><th>姓名</th><th>职务</th><th>合计薪酬（千美元）</th><th>说明</th></tr>
<tr><td>贫炜祎</td><td>执行董事</td><td class="n">3,223</td><td class="q">—</td></tr>
<tr><td>周彧聪</td><td>执行董事</td><td class="n">1,318</td><td class="q">—</td></tr>
<tr><td>赵鹏宇</td><td>执行董事</td><td class="n">896</td><td class="q">—</td></tr>
<tr><td>闵俊杰</td><td>主席、首席执行官</td><td class="n">241</td><td class="q">—</td></tr>
</table>
<div class="note">2022 年采纳 IPO 前股份激励计划，向 392 名承授人授出购股权认购 20,890,736 股 A 类普通股，股份已预先发行予雇员持股平台，归属不摊薄现有股东；IPO 后计划剩余年期约九年零八个月。</div>

### 分红与股东回报
<div class="note">报告期内股份未在联交所上市，未派息，无购回上市证券，无股权挂钩协议。</div>

### 关联体系
<table>
<tr><th>关联方</th><th>关系</th><th>风险点</th></tr>
<tr><td>阿里巴巴</td><td class="q">持续关连交易：主API服务、云端服务、业务合作</td><td class="q">算力与云服务依赖关联方，采购定价是否公允需跟踪</td></tr>
</table>""",
    },
    {
        "pages": """姓名陈维良国籍中国是否取得其他国家或地区居留权否主要职业及职务沐曦集成电路(上海)股份有限公司法定代表人、董事长、总经理过去10年曾控股的境内外上市公司情况无。控股股东：上海骄迈企业咨询合伙企业(有限合伙)，单位负责人陈维良，属于持股平台。

报告期内从公司获得的税前薪酬总额（万元）：陈维良320.33、彭莉240.39、杨建227.42、王爽175.39、魏忠伟147.81。全体董事、高级管理人员和核心技术人员税前报酬合计1,449.86万元。

公司于2025年实施股权激励计划、员工持股计划或其他员工激励措施的情况：股权激励总体情况□适用 √不适用。公司于2026年3月25日召开董事会，审议通过了《关于2025年度利润分配方案的议案》，根据《公司章程》规定，公司本年度尚未盈利，尚不满足利润分配条件，因此公司2025年度拟不进行利润分配。""",
        "output": """### 控制权与利益绑定
<table>
<tr><th>问题</th><th>事实</th><th>含义</th></tr>
<tr><td>谁控制公司</td><td class="q">实际控制人陈维良（法定代表人、董事长兼总经理），通过持股平台上海骄迈控制公司</td><td class="q">控制权与经营权集中于创始人</td></tr>
<tr><td>管理层从哪来</td><td class="q">核心管理层为技术出身团队，陈维良过去十年无其他控股上市公司</td><td class="q">—</td></tr>
</table>

### 薪酬与激励
<table>
<tr><th>姓名</th><th>职务</th><th>税前薪酬（万元）</th><th>说明</th></tr>
<tr><td>陈维良</td><td>董事长、总经理</td><td class="n">320.33</td><td class="q">—</td></tr>
<tr><td>彭莉</td><td>—</td><td class="n">240.39</td><td class="q">—</td></tr>
<tr><td>杨建</td><td>—</td><td class="n">227.42</td><td class="q">—</td></tr>
<tr><td>王爽</td><td>—</td><td class="n">175.39</td><td class="q">—</td></tr>
<tr><td>魏忠伟</td><td>—</td><td class="n">147.81</td><td class="q">—</td></tr>
</table>
<div class="note">全体董事、高管及核心技术人员税前报酬合计 1,449.86 万元；报告期内无股权激励计划、员工持股计划。</div>

### 分红与股东回报
<div class="note">公司尚未盈利，不满足章程规定的分红条件，2025 年度拟不进行利润分配。</div>

### 关联体系
<table>
<tr><th>关联方</th><th>关系</th><th>风险点</th></tr>
<tr><td>华泰创新投资</td><td class="q">保荐机构子公司，战略配售 955,474 股，2027 年 12 月 17 日可上市交易</td><td class="q">—</td></tr>
</table>""",
    },
]



def _governance_prompt() -> str:
    return textwrap.dedent(f"""
        <requirement>
        你在阅读一份上市公司年报，蒸馏对股东最重要的治理与激励事实，按四个 ### 小节组织：
        控制权与利益绑定、薪酬与激励、分红与股东回报、关联体系。

        - 数字照抄带单位，禁止编撰；不罗列委员会名单与成员，不做员工构成表；不输出页码和章节名
        - 全文用简体中文
        </requirement>

        <examples>
        {"\n".join(f"<example>\\n<pages>\\n{x['pages']}\\n</pages>\\n<output>\\n{x['output']}\\n</output>\\n</example>" for x in _governance_examples)}
        </examples>""").strip()


_pick_task = "你在通读一份上市公司年报的目录。了解公司治理与激励：实际控制人、高管薪酬、股权激励计划、分红政策、关联交易，需要阅读哪些章节？"


def gen_governance(agent: Agent, stock: str, market: str, root: Path, force: bool) -> tuple[int, str]:
    # 治理与激励分析只需要最新一份年报
    derived = root / market / stock / "derived"
    mds = sorted(derived.glob("*.md"))
    if not mds:
        raise SystemExit(f"{derived} 没有年报 md，先跑 ocr/cli.py")
    md_path = mds[-1]
    year = report_year(agent, md_path)

    toc = Toc(agent, md_path)
    logger.info(f"阅读: {md_path.name}")

    out_path = root / market / stock / "knowledge" / f"fy{year}" / "governance.json"
    if out_path.exists() and not force:
        logger.info(f"跳过（已存在）: {out_path}")
        return year, json.loads(out_path.read_text(encoding="utf-8"))["doc"]

    logger.info(f"{year} …")
    picked = toc.pick_chapters(toc.chapters, _pick_task)
    logger.info(f"选中章: {[toc.chapters[i]['title'] for i in picked]}")
    chunks = [chunk for i in picked for chunk in toc.chapter_text_chunk(i)]

    doc = agent.chat_reduce_chunks(_governance_prompt(), chunks)
    if not doc.strip():
        raise ValueError(f"{year} governance 分析异常")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps({"year": year, "topic": "governance", "source": toc.gen_source(picked), "doc": doc}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    logger.info(f"输出: {out_path}")
    return year, doc


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S", stream=sys.stderr)
    parser = argparse.ArgumentParser(description="governance 模块：蒸馏治理与激励，产物 knowledge/fy<year>/governance.json")
    parser.add_argument("--stock", default="601633")
    parser.add_argument("--market", default="cn", choices=["cn", "hk"])
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1] / ".cagent"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    agent = Agent(trace=True)
    gen_governance(agent, args.stock, args.market, Path(args.root), args.force)

    from cagent.wiki_render import render_module

    html_path = render_module(Path(args.root), args.market, args.stock, "governance", Path("output/wiki") / f"{args.market}_{args.stock}" / "governance.html")
    logger.info(f"渲染: {html_path}")


if __name__ == "__main__":
    main()
