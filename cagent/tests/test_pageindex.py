"""索引验证：为本地最新一份公告构建页码索引（.pageindex.json 构建产物）并做结构自检。

    uv run python cagent/tests/test_pageindex.py --market cn --code 601633 [--force] [--trace]

两类判据：
1. 结构自检（形式正确）：页码索引非空且页码单调、叶节点带摘要、产物与原文一致、
   阅读单元覆盖全文且每页恰一次。
2. **LLM 问答（为最终效果服务）**：索引存在的意义是「把问题路由到能读完的那几页」。
   只给目录（标题+摘要+页码区间）让模型选单元 → 选中的单元必须覆盖 GT 页，且要小到
   ≤_READ_PARTS 段能顺序读完；再让模型在该单元原文里作答 → 数值须与 GT 一致、
   引文须逐字出现在它自己报的页码上（反幻觉）。
   答不出就是索引没用——结构全对也不算好。FAIL 是诊断（第一个 FAIL 即本轮目标），
   不是噪声：例如财务报告整节 28 段无法细分，任何附注问题都答不了。

GT 页码与数值取自年报原文并人工核对（同一事实出现在多页时全部列出，路由到任一都对）。
"""

import json

import dspy

from cagent.lib.lakehouse import PAGE_RE, LakehouseReader
from cagent.llm import DETERMINISTIC, tag
from cagent.pageindex.builder import PageIndexBuilder
from cagent.pageindex.reader import PageIndexReader, pageindex_is_current
from cagent.tests._util import base_parser, connect, pick_filing, report, with_llm


class RouteQuestion(dspy.Signature):
    """只根据章节目录（标题、一句话摘要、页码区间）判断：回答这个问题该读哪个单元。

    选最可能的一个；目录里完全看不出答案在哪一节时选 0。不要凭常识猜公司情况。
    """

    catalog: str = dspy.InputField(desc="章节目录，每行：序号. 标题（p.起-止）摘要")
    question: str = dspy.InputField(desc="要回答的问题")
    unit: int = dspy.OutputField(desc="单元序号，0 表示目录无法定位")


class AnswerFromUnit(dspy.Signature):
    """在给定原文片段里找答案。片段里没有就 found=false，不要用自己知道的信息作答。

    page 填答案所在页的物理页码（片段里 `<!-- page N -->` 的 N）；quote 从原文逐字抄
    一句含答案的话（不要改写数字、不要补标点）。
    """

    passage: str = dspy.InputField(desc="章节原文片段（含物理页码标记）")
    question: str = dspy.InputField(desc="要回答的问题")
    found: bool = dspy.OutputField(desc="答案是否就在这一段里")
    answer: str = dspy.OutputField(desc="直接答案，带原文单位")
    page: int = dspy.OutputField(desc="答案所在物理页码")
    quote: str = dspy.OutputField(desc="逐字引文（≤60 字）")


_READ_PARTS = 3    # 一个单元最多顺序读几段（再多就不是「靠索引定位」而是通读了）

# 问答案例：(问题, GT 页码——同一事实印在几页就列几页, GT 数值原文串)
# 每题都回原文核过：致辞里 3,439.96 万辆是行业产销、公司新车销量才是 132.38 万辆；
# 「不含对子公司担保」的第一笔是 7,588,320.00（p.111），1,616,624,000 属于对子公司担保表。
_QA_GWM: list[tuple[str, list[int], str]] = [
    ("主要会计数据表里，2025 年度营业总收入是多少（万元口径）？", [8], "22,282,423.85"),
    ("董事长致辞中提到的公司 2025 年新车销量是多少万辆？", [13], "132.38"),
    ("截至 2025 年末，公司对外担保（不包括对子公司的担保）明细里第一笔的担保金额是多少？",
     [111], "7,588,320.00"),
    ("报告期末可转换公司债券的最新转股价格是多少元？", [134, 135], "39.16"),
    ("报告期末母公司在职员工的数量是多少人？", [60], "44,464"),
    ("本年度关键管理人员薪资（报酬）是多少元？", [268, 303], "18,249,600.96"),
]
QA_CASES: dict[tuple[str, str], list[tuple[str, list[int], str]]] = {("cn", "601633"): _QA_GWM}


_SEP_CHARS = " \t\n|｜"


def _plain(s: str) -> str:
    """去标签、去分隔符（表格文字在 <td> 里，模型跨格引用会插「|」——都不算语义差异）。"""
    out: list[str] = []
    while True:
        i = s.find("<")
        if i < 0:
            out.append(s)
            break
        out.append(s[:i])
        j = s.find(">", i)
        if j < 0:
            break
        s = s[j + 1:]
    return "".join(c for c in "".join(out) if c not in _SEP_CHARS)


def qa_checks(tr: PageIndexReader, md, reader: LakehouseReader,
              market: str, code: str) -> list[tuple[str, bool, str]]:
    """LLM 问答：只给目录能不能路由到正确的那几页，能不能在小单元原文里答对且不编造引文。"""
    cases = QA_CASES.get((market, code))
    if not cases:
        print(f"（{market}/{code} 无内置问答样例，只跑结构自检）")
        return []
    units = tr.reading_units(12000) or []
    nodes = tr.load() or []
    catalog = "\n".join(
        f"{k}. {u.title}（p.{u.start}-p.{u.end}）"
        + "；".join(n.summary for n in nodes if u.start <= n.start <= u.end and n.summary)
        for k, u in enumerate(units, 1))
    pages = reader.pages_of(md.read_text(encoding="utf-8"))
    route, answer = dspy.Predict(RouteQuestion), dspy.Predict(AnswerFromUnit)
    print("\n问答判据（只给目录，能否定位到能读完的那几页）：")
    out: list[tuple[str, bool, str]] = []
    for q, gts, want in cases:
        name = f"问答·{q[:16]}"
        with tag(f"pageindex:route:{code}"):
            picked = route(catalog=catalog, question=q,
                           config=DETERMINISTIC | {"max_tokens": 60}).unit
        u = units[picked - 1] if 1 <= picked <= len(units) else None
        if u is None:
            out.append((name, False, f"目录无法定位（选 {picked}）"))
            continue
        if not any(u.start <= pg <= u.end for pg in gts):
            out.append((name, False,
                        f"路由错：选中「{u.title[:18]}」p.{u.start}-{u.end}，GT 页 {gts}"))
            continue
        if len(u.parts) > _READ_PARTS:
            out.append((name, False,
                        (f"路由对但索引不够细：该单元 {len(u.parts)} 段（>{_READ_PARTS}），"
                         f"只能通读，定位无意义")))
            continue
        got = None
        for j, part in enumerate(u.parts, 1):
            with tag(f"pageindex:answer:{code}#{u.start}.{j}"):
                res = answer(passage=part, question=q,
                             config=DETERMINISTIC | {"max_tokens": 400})
            if res.found:
                got = res
                break
        if got is None:
            out.append((name, False, f"路由对（读完 {len(u.parts)} 段）但没答出来"))
            continue
        page = _plain(pages.get(got.page, ""))
        number_ok = want in got.answer.replace(" ", "")
        # 页码出处必须能回原文核对：所引用那一页确实印着这个数，且引文是逐字抄的
        cited = want in page
        verbatim = bool(got.quote) and _plain(got.quote) in page
        out.append((name, number_ok and cited and verbatim,
                    (f"p.{u.start}-{u.end}（{len(u.parts)}段）→「{got.answer[:24]}」"
                     + ("数值对" if number_ok else f"数值不符，GT {want}")
                     + ("，页码可核" if cited else f"，页码对不上(p.{got.page})")
                     + ("，引文逐字" if verbatim else f"，引文非逐字「{got.quote[:24]}」"))))
    return out


def main() -> None:
    p = base_parser("索引：页码索引构建、结构自检与 LLM 问答")
    p.add_argument("--force", action="store_true", help="已有 .pageindex.json 也强制重建")
    args = p.parse_args()

    reader = LakehouseReader()
    filing, derived = pick_filing(reader, args.market, args.code, args.file)
    md = derived / filing["file"]
    print(f"公告: {filing['date']} {filing['title']}（{filing['file']}）")

    def run() -> None:
        connect(args.trace, "pageindex")
        toc_path = PageIndexBuilder(reader).build(md, force=args.force)

        tree = json.loads(toc_path.read_text(encoding="utf-8"))["structure"]
        pages = reader.pages_of(md.read_text(encoding="utf-8"))
        last = max(pages)
        tr = PageIndexReader(md, reader)
        nodes = tr.load() or []
        units = tr.reading_units(12000) or []

        coverage: dict[int, int] = {}
        for u in units:
            for m in PAGE_RE.finditer("\n\n".join(u.parts)):
                n = int(m.group(1))
                coverage[n] = coverage.get(n, 0) + 1
        missing_pages = [n for n in pages if coverage.get(n) != 1]
        overlapped = [n for n, c in coverage.items() if c > 1]
        checks = [
            ("产物与原文一致（构建产物新鲜）", pageindex_is_current(md, reader),
             "缺失/损坏/页数变化会自动重建"),
            ("页码索引结构非空", bool(tree), f"{len(tree)} 个顶层节点"),
            ("页码在文档范围内且单调", all(1 <= n.start <= n.end <= last for n in nodes),
             f"{len(nodes)} 节点，文档 {last} 页"),
            ("叶节点带摘要", all(bool(n.summary) for n in nodes), "每节点一句话摘要"),
            ("可切分阅读单元", bool(units),
             f"{len(units)} 个单元，共 {sum(len(u.parts) for u in units)} 段"),
            ("阅读单元覆盖全文且每页恰一次",
             not missing_pages and not overlapped,
             f"缺页 {missing_pages[:5]}，重复页 {overlapped[:5]}"),
        ]
        print("\n页码索引前 5 个节点:")
        for n in nodes[:5]:
            print(f"  {n.node_id} {n.title}（p.{n.start}-p.{n.end}）{n.summary[:40]}")
        checks += qa_checks(tr, md, reader, args.market, args.code)
        print()
        report(checks)

    with_llm(run)


if __name__ == "__main__":
    main()
