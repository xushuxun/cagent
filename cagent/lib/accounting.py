"""定量：年报原文 → 结构化财务表格 + 概念数字表（财务数字的唯一出处，金额统一为亿）。

第一性原理：公告里的每个数字都是一条断言——「某主体、某口径、某期间、某量纲下等于多少」。
所以提取产物必须保住这四元关系，并且标明每个数字**能被什么验证**（见 VERIFY_*）：

    tied       与主表同科目同期间勾稽上——受会计准则约束，两条独立取数路径互相印证
    sum        表内合计恒等式成立——这张表自己声明的加总关系，程序算出来对得上
    unverified 说明性表格：产销量、员工、折旧年限、税率…没有会计勾稽可验，
               只剩量纲与格式检查。不可验证不等于不可用，但绝不允许当成已验证的财务数字。

分工：程序做直解、结构分析、定标、算术与证据判定；LLM 只做语义标注（这张表在讲什么、
是不是财务口径、合并还是母公司、哪个数值列对应哪个期间）——读数字但不口算。
概念数字表（values/extra，供估值与装配取数）与财务表格集同源于一次直解。
估值分析在 lib/valuation.py。无缓存：每次全量重算。
"""

import bisect
import logging
from dataclasses import dataclass, field
from pathlib import Path

import dspy
from pydantic import BaseModel

from cagent.llm import DETERMINISTIC, tag

logger = logging.getLogger(__name__)


# ---- 单元格数字：raw 逐字取自原文，换算与定标全由程序做，不做任何单位猜测 ----

# 单位字（长的在前，避免"亿元"被"元"截胡）→ 折算到亿的系数。查找表，不是规则匹配。
DECL_SCALE = {"百萬元": 1e-2, "百万元": 1e-2, "千元": 1e-5, "萬元": 1e-4, "万元": 1e-4,
              "億元": 1.0, "亿元": 1.0, "元": 1e-8}
_UNIT_CHARS = "元"          # 单元格里的单位字都以「元」结尾，先就近试这几个
_CURRENCY_WORDS = {"人民币": "CNY", "人民幣": "CNY", "港币": "HKD", "港幣": "HKD",
                   "港元": "HKD", "美元": "USD", "美金": "USD", "欧元": "EUR", "歐元": "EUR"}
# 占位符单元格：OCR 用「-」「/」「.」表示无数字——既不是数据，也不该进行标签
_BLANK_STRIP = " \t-—–/／·.,，。、"
# 摘掉外壳后允许剩下的字符（括号、百分号、破折号占位）
_SHELL_CHARS = "()（）%-—– "


def _blank(c: str) -> bool:
    return not c.strip(_BLANK_STRIP)


def parse_number(raw: str) -> tuple[float, float | None, bool] | None:
    """解析原文数字串，返回 (数字, 自带单位折算到亿的系数或 None, 是否百分数)；读不出返回 None。

    以 `float()` 为唯一判据：摘掉括号外壳、百分号、千分位与紧邻的金额单位字之后，
    剩下的必须整个是个数——「2024年末调整后」「注2」「(六)1」这类残留、
    「173,200,174.172.62」这类粘连、「十七,320,017.42」这类错读，一律拒收。
    宁可丢一格并披露，不编一个数。不猜单位：没带单位字时系数为 None，定标由上层负责。
    """
    s = raw.strip()
    if not s:
        return None
    is_pct = s.endswith("%")
    negative = s.startswith(("(", "（")) and s.endswith((")", "）"))
    core = s.strip(_SHELL_CHARS)
    scale = None
    for word in _UNIT_WORDS_SORTED:          # 紧邻单位字优先于裸数字（"万元" 不能被当 "元"）
        if core.endswith(word) and len(core) > len(word):
            scale, core = DECL_SCALE[word], core[:-len(word)].strip(_SHELL_CHARS)
            break
    # 「1.234,56」这类欧式写法去掉千分位会变成另一个数，直接拒收
    if "," in core and "." in core and core.index(",") > core.index("."):
        return None
    core = core.replace(",", "").replace(" ", "")
    try:
        digits = float(core)
    except ValueError:
        return None
    if negative:
        digits = -abs(digits)
    return digits, scale, is_pct


# 单位字按长度降序：先试「百萬元」再试「元」
_UNIT_WORDS_SORTED = sorted(DECL_SCALE, key=len, reverse=True)


def scale_of_unit(unit: str) -> float | None:
    """单位字 → 折算到亿的系数。查表，不匹配文本——声明由模型从表前后的文字里读出来。"""
    return DECL_SCALE.get(unit.strip())


def same_value(a: float, b: float) -> bool:
    """两个已归一化的值是否同一数字（0.01% 容差，只吸收不同表格的小数位舍入差；
    重述/错格的真分歧必须暴露为冲突）。"""
    return abs(a - b) <= max(1e-6, abs(b) * 1e-4)


# ---- 直解 HTML 表格：按标签切分（不用正则），并按 colspan/rowspan 摊平合并格 ----
#
# OCR 产物里只有 <table>/<tr>/<td>（<td> 可带 colspan/rowspan），标签是配平的。
# 合并结构是原文自带的事实：摊平之后表头的期间分层与跨行科目名都能按列直接读出来，
# 不需要猜跨列宽度，也不需要模型誊抄列名。表前后的文字（表题、单位声明、「注：…」）
# 一并留档——附注里的说明文字往往在表格之后，只截表前会把它们记到下一张表头上。

_PAGE_MARK = "<!-- page "       # lakehouse 约定的物理页码标记（见 lib/lakehouse.py）
_PROSE_CHARS = 600              # 表前/表后各留多少原文字符（表题与「注」都在这个尺度里）


def _text(s: str) -> str:
    """去掉标签只留文字（未闭合的标签从它这里截断），并压平空白。"""
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
    return " ".join("".join(out).split())


def _span(tag: str, attr: str) -> int:
    """从 `colspan="3"` 这类标签文本里取整数；没有该属性或读不出则 1。"""
    k = tag.find(attr)
    if k < 0:
        return 1
    digits = ""
    for ch in tag[k + len(attr):]:
        if ch.isdigit():
            digits += ch
        elif digits:
            break
    return int(digits) if digits.isdigit() and int(digits) > 1 else 1


def _cells(row: str) -> list[tuple[str, int, int]]:
    """一行的单元格：(文字, colspan, rowspan)。"""
    out: list[tuple[str, int, int]] = []
    for piece in row.split("<td")[1:]:
        tag, _, body = piece.partition(">")
        out.append((_text(body.split("</td>")[0]), _span(tag, "colspan"), _span(tag, "rowspan")))
    return out


def _expand(rows: list[list[tuple[str, int, int]]]) -> list[list[str]]:
    """按 colspan/rowspan 摊平成等宽行——合并格在它覆盖的每个位置重复自己的文字。"""
    carry: dict[int, tuple[int, str]] = {}       # 列号 -> (还要占几行, 文字)
    grid: list[list[str]] = []
    for cells in rows:
        row: list[str] = []
        col = i = 0
        while i < len(cells) or (carry and col <= max(carry)):
            if col in carry:                     # 上一行跨下来的格子先占位
                left, text = carry.pop(col)
                row.append(text)
                if left > 1:
                    carry[col] = (left - 1, text)
                col += 1
                continue
            text, cs, rs = cells[i]
            i += 1
            for _ in range(cs):
                row.append(text)
                if rs > 1:
                    carry[col] = (rs - 1, text)
                col += 1
        if any(r.strip() for r in row):
            grid.append(row)
    return grid


def _join_wrapped(rows: list[list[str]]) -> list[list[str]]:
    """拼接被 OCR 拆成两行的长科目名（港股利润表/现金流量表里实测存在）。

    判据是结构而非词表：前一行只有首格有字、且不以冒号结尾（冒号结尾是「流动资产:」
    分组行）、后一行同宽且首格有字并带数值 ⇒ 前一行是断开了一半的科目名，并进去。
    不这么做的代价是真实的：hk 年报「購建固定資產、無形資產和／其他長期資產支付的現金」
    被拆成两行后，capex 只剩一个无数值的空行，资本开支就此丢失。
    """
    out: list[list[str]] = []
    for r in rows:
        prev = out[-1] if out else []
        if (len(prev) > 1 and prev[0] and not any(prev[1:]) and not prev[0].endswith((":", "："))
                and len(r) == len(prev) and r[0] and any(parse_number(c) for c in r[1:])):
            out[-1] = [prev[0] + r[0], *r[1:]]
        else:
            out.append(r)
    return out


def _page_marks(text: str) -> list[tuple[int, int]]:
    """[(偏移, 页码)]，按 `<!-- page N -->` 字面标记切出（页码是物理页，1 起）。"""
    out: list[tuple[int, int]] = []
    pos = 0
    while True:
        i = text.find(_PAGE_MARK, pos)
        if i < 0:
            return out
        digits = ""
        for ch in text[i + len(_PAGE_MARK):]:
            if ch.isdigit():
                digits += ch
            elif digits:
                break
        if digits:
            out.append((i, int(digits)))
        pos = i + len(_PAGE_MARK) + len(digits)


@dataclass
class RawTable:
    """程序直解的一张表：摊平后的等宽行 + 页码 + 表前/表后原文（表题、单位声明、「注」）。"""

    rows: list[list[str]]
    page: int | None = None
    before: str = ""
    after: str = ""


def extract_tables(text: str) -> list[RawTable]:
    """从公告原文直解全部表格（纯程序，不过 LLM）。"""
    marks = _page_marks(text)
    offsets = [o for o, _ in marks]
    out: list[RawTable] = []
    pos = prev_end = 0
    while True:
        start = text.find("<table>", pos)
        if start < 0:
            return out
        close = text.find("</table>", start)
        nxt = text.find("<table>", start + len("<table>"))
        # 未闭合的表不能吞掉后文：遇到下一个 <table> 就停
        if close < 0 or (0 <= nxt < close):
            body, end = text[start + 7:nxt if nxt >= 0 else len(text)], (
                nxt if nxt >= 0 else len(text))
        else:
            body, end = text[start + 7:close], close + len("</table>")
        # 表前/表后只取相邻两表之间的散文：表后的「注：…」属于这张表，
        # 但下一张表的数字绝不能算进这张表的注里。
        after_end = nxt if nxt >= 0 else len(text)
        rows = _join_wrapped(_expand([_cells(r) for r in body.split("<tr>")[1:]]))
        at = bisect.bisect_right(offsets, start) - 1
        out.append(RawTable(rows=rows, page=marks[at][1] if at >= 0 else None,
                            before=_text(text[max(prev_end, start - _PROSE_CHARS):start]),
                            after=_text(text[end:after_end])[:_PROSE_CHARS]))
        prev_end, pos = end, end


# ---- 表级结构分析：表头块与列定类。只按数值形态划界，不看科目名 ----

_HEADER_MAX = 3        # 前导表头行上限（OCR 把跨行列头摊平成连续的表头行）
_VALUE_DENSITY = 0.5   # 一列的非空单元格里数值占比达到即数值列
_AMOUNT_SHARE = 0.5    # 数值列里「金额形态」单元格占比达到即金额列


def _is_amount(raw: str, digits: float) -> bool:
    """金额形态：带千分位，或量级 ≥ 千（附注编号、股数、天数是小额无分隔裸数字）。"""
    return "," in raw or abs(digits) >= 1000


@dataclass
class ColSpec:
    """一列的实测结论：列号、表头分层路径、期间年份、值形态（amount/ratio/count/text）。"""

    i: int
    path: list[str] = field(default_factory=list)
    year: str | None = None
    kind: str = "text"

    @property
    def name(self) -> str:
        """最具体的一层（子表头优先，如「账面价值」）。"""
        return self.path[-1] if self.path else ""

    @property
    def label(self) -> str:
        """全路径（「2025年12月31日(经审计)/账面价值」）——列语义照抄原文，不重写。"""
        return "/".join(self.path)

    @property
    def is_value(self) -> bool:
        return self.kind != "text"


@dataclass
class Shape:
    """一张表的结构：表头块原文行（照抄，含对不齐的子表头）、列定义、数据行。"""

    header: list[list[str]]
    cols: list[ColSpec]
    rows: list[list[str]]

    @property
    def value_cols(self) -> list[ColSpec]:
        return [c for c in self.cols if c.is_value]

    @property
    def flat(self) -> bool:
        """单行表头：列头本身就是期间，年份读得出来。"""
        return len(self.header) == 1


def _header_rows(rows: list[list[str]]) -> int:
    """前导表头行数：整行零数值且至少两格有字。

    「流动资产:」这类分组行只有一格有字，是数据行不是表头——分组行被当成表头会把
    整段结构吃掉。
    """
    n = 0
    for r in rows:
        filled = [c for c in r if not _blank(c)]
        if n < _HEADER_MAX and len(filled) >= 2 and not any(parse_number(c) for c in filled):
            n += 1
        else:
            break
    return n


def _one_year(years: set[str]) -> str | None:
    """路径里只有一个年份时用它定期间；多个或零个都不给（不猜归属）。"""
    return next(iter(years)) if len(years) == 1 else None


def _col_year(s: str) -> str | None:
    """文字里唯一的四位数年份（「2025年12月31日(经审计)」「2025年度」→ 2025）；否则 None。

    只认孤立出现的四位数且落在 1990-2100：「1,234」这类千分位、「8-40」这类区间都不算。
    """
    runs, cur = [], ""
    for ch in s + " ":
        if ch.isdigit():
            cur += ch
        else:
            if cur:
                runs.append(cur)
            cur = ""
    years = [x for x in runs if len(x) == 4 and 1990 <= int(x) <= 2100]
    return years[0] if len(years) == 1 else None


def shape_of(t: RawTable) -> Shape:
    """一张表的结构分析（纯程序）：表头块、逐列路径与定类、数据行。

    行已按 colspan/rowspan 摊平成等宽，所以每列的表头路径就是原文的分层语义
    （「2025年12月31日(经审计) / 账面余额 / 金额」），不需要猜合并宽度、也不需要模型
    誊抄。路径里只有一个年份时才给列定期间（「本年增加」这类相对期间留空）。
    """
    h = _header_rows(t.rows)
    head, data = t.rows[:h], t.rows[h:]
    ncol = max((len(r) for r in t.rows), default=0)
    cols: list[ColSpec] = []
    for i in range(ncol):
        path: list[str] = []
        for row in head:
            cell = row[i].strip() if i < len(row) and not _blank(row[i]) else ""
            if cell and (not path or path[-1] != cell):
                path.append(cell)
        years = {y for y in (_col_year(x) for x in path) if y}
        cells = [r[i] for r in data if i < len(r) and not _blank(r[i])]
        pairs = [(c, p) for c in cells if (p := parse_number(c))]
        if not cells or len(pairs) / len(cells) < _VALUE_DENSITY:
            cols.append(ColSpec(i=i, path=path, year=_one_year(years)))
            continue
        pct = sum(1 for _c, (_d, _s, ip) in pairs if ip) / len(pairs)
        amt = sum(1 for c, (d, s, _ip) in pairs if s is not None or _is_amount(c, d))
        kind = ("ratio" if pct >= 0.5 or any("%" in x for x in path) else
                "amount" if amt / len(pairs) >= _AMOUNT_SHARE else "count")
        cols.append(ColSpec(i=i, path=path, year=_one_year(years), kind=kind))
    return Shape(header=head, cols=cols, rows=data)


# ---- 语义标注（LLM）：这张表在讲什么、是不是财务口径、合并还是母公司 ----
#
# 模型的职责到此为止。列的期间不用它说：列头自己印着年份（`_col_year`/`_align_headers`
# 只认能证明的对齐，跨列合并到无法归属的列一律不给年份——宁可少给，不给错期间）。
# 让模型逐列誊名换来的收益，抵不上它在 CPU 推理服务上的耗时（实测一批 25 表 220 秒）。

# 主表（概念数字表的来源）+ 附注/指标/经营表；说明性表格照样结构化，只是没有会计勾稽可验
STMT_KINDS = {"bs", "is", "cf", "cfs"}      # 资产负债表/利润表/现金流量表/现金流量表补充资料
CATEGORIES = STMT_KINDS | {"se", "note", "kpi", "ops"}
ENTITIES = {"con", "par"}


class TableAnno(BaseModel):
    """一张财务表的语义标注。"""

    idx: int
    category: str      # bs/is/cf/cfs/se 主表 | note 报表项目注释 | kpi 会计数据与指标 | ops 经营数值
    title: str         # 这张表在讲什么（照抄原文写法的科目名，不含页眉、附注编号与「- 续」）
    entity: str = "con"    # con 合并口径 / par 母公司（公司报表及其注释）口径
    unit: str = ""     # 表前/表后印刷的金额单位词（元/千元/万元/百万元/亿元）；非金额表留空
    currency: str = ""   # 币种（CNY/HKD/USD/EUR）；读不出留空


class AnnotateTables(dspy.Signature):
    """把年报里的财务类表格挑出来并标注语义（不是财务表就不要出现在输出里）。

    category：bs 资产负债表 / is 利润表 / cf 现金流量表 / cfs 现金流量表补充资料 /
    se 股东权益变动表 / note 财务报表项目注释与明细表 / kpi 主要会计数据与财务指标
    （含分季度、每股收益、净资产收益率、非经常性损益）/ ops 经营性数值表（产销量、
    产能、员工人数、折旧年限、税率等）。释义、承诺、会议决议、任职情况、审计意见、
    备查文件这类没有财务数字的表不标。

    title 写这张表在讲什么，去掉页眉、公司名、附注编号与「- 续」，**写法照抄原文**（港股
    正文是繁体就写繁体）——附注表名要与被注释的报表科目同名，跨表勾稽靠这个同名对上，改写成
    另一种字体就接不上了。一张长表被 OCR 切成续页时，续页单独标注且 title/category/entity
    与起始页完全一致（否则拼不回去）。
    entity：合并口径 con，母公司（「公司报表」及其注释）口径 par。

    主表（bs/is/cf/cfs/se）只有财务报告章节里逐科目列示、通常带「附注」列的那几张。
    管理层讨论与分析里的「××科目变动分析表」「资产及负债状况」「费用」「研发投入情况表」
    「产销量」「产能」是摘要或经营数据，标 kpi/ops，不要标成主表——它们会污染报表口径。

    unit 抄表前/表后印刷的金额单位词（「人民币元」「单位：千元」「RMB\'000」都算，只写
    单位字：元/千元/万元/百万元/亿元）；产销辆数、员工人数、折旧年限这类非金额表留空。
    currency 填币种代码（CNY/HKD/USD/EUR）。这两个字段决定数字的量级，读不出就留空，别猜。
    """

    digests: str = dspy.InputField(
        desc="本批表格摘要，每行：#序号｜p页码｜表前文字｜H表头行｜V数值列(列号=程序读到的列名)｜D数据行")
    tables: list[TableAnno] = dspy.OutputField(
        desc="财务类表格标注，按序号升序，一张表一行；不输出任何数字")


def _digest(i: int, t: RawTable, sh: Shape) -> str:
    """一张表的摘要行（供语义标注）：页码、表前文字、表头分层、数据行、表后原文。

    表后的「注：…」与下一段的文字必须一起给——单位口径、共线生产、口径调整这类限定
    条件都印在那里，只看表内数字会读出与披露原意不符的数（这是判据的一部分：模型要能
    看到人类看得到的一切）。列路径 `V[列号=期间/子项]` 是程序按 colspan/rowspan 摊平后
    的事实，模型据此判断这是不是主表、是不是母公司口径。
    """
    def cells(r: list[str]) -> str:
        return "|".join(c[:22] for c in r[:8])
    parts = [f"#{i}", f"p{t.page if t.page is not None else '?'}",
             "前:" + t.before[-90:], "后:" + t.after[:90]]
    parts += [f"H{k}[{cells(h)}]" for k, h in enumerate(sh.header)]
    parts.append("V[" + ",".join(f"{c.i}={c.label[:26]}" for c in sh.value_cols) + "]")
    parts += [f"D{k}[{cells(d)}]" for k, d in enumerate(sh.rows[:2])]
    return "｜".join(parts)


_TABLE_BATCH = 25      # 每批标注的表数（摘要约 6k 字符；批小则单批失败丢的表少）
_TABLE_BATCH_OUT = 4000

# one-shot 示教：摘要行全部取自真实年报（cn 601633 2025 年报直解产物）。
# 教的是判据而不是个例——董事会会议次数表不出现在输出里（有数字但不是财务口径）、
# 主表与续页 title 必须一致、合并与母公司分得开、多行表头按 V 给出的列号逐列补语义。
# 列语义示例（#10/#161）已逐格核对原文：如 #161 col1=2025账面余额、col4=计提比例、
# col6=2024账面余额。拿不准的表（#148 权益变动表）教「留空」而非硬编列名。
_ANNOTATE_DEMO_DIGESTS = [
    '#10｜p8｜前:7 长城汽车股份有限公司 2025 年年度报告 七、近五年主要会计数据和财务指标 (一) 主要会计数据 单位：万元 币种：人民币｜后:8 长城汽车股份有限公司 2025 年年度报告｜H0[主要会计数据|2025年|2024年|2024年|本期比上年同期增减(%)|2023年|2023年|2022年]｜H1[主要会计数据|2025年|调整后|调整前|本期比上年同期增减(%)|调整后|调整前|调整后]｜V[1=2025年,2=2024年/调整后,3=2024年/调整前,4=本期比上年同期增减(%),5=2023年/调整后,6=2023年/调整前,7=2022年/调整后,8=2022年/调整前,9=2021年/调整后,10=2021年/调整前]｜D0[营业总收入|22,282,423.85|20,219,377.96|20,219,547.23|10.20|17,320,017.42|17,321,207.68|13,733,998.52]｜D1[营业收入|22,282,423.85|20,219,377.96|20,219,547.23|10.20|十七,320,017.42|十七,321,207.68|十七,733,998.52]',
    '#26｜p28｜前:售模式情况的说明 公司主营为整车及主要汽车零部件的研发、生产、销售，公司主营业务归属汽车行业，产品分为整车、零部件、模具、劳务及其他。 (2). 产销量情况分析表 √适用 □不适用｜后:(3). 重大采购合同、重大销售合同的履行情况 □适用√不适用 28 长城汽车股份有限公司 2025 年年度报告 (4). 成本分析表 单位：元 币种：人民币｜H0[主要产品|单位|生产量|销售量|库存量|生产量比上年增减(%)|销售量比上年增减(%)|库存量比上年增减(%)]｜V[2=生产量,3=销售量,4=库存量,5=生产量比上年增减(%),6=销售量比上年增减(%),7=库存量比上年增减(%)]｜D0[皮卡|辆|177,035|178,936|15,730|6.72|2.22|52.04]｜D1[SUV|辆|958,920|1,033,097|80,775|-1.65|4.45|-7.89]',
    '#56｜p57｜前:连续两次未亲自出席董事会会议的说明 □适用√不适用 56 长城汽车股份有限公司 2025 年年度报告｜后:(二) 董事对公司有关事项提出异议的情况 □适用√不适用 (三) 其他 □适用 √不适用 五、董事会下设专门委员会情况 √适用 □不适用 （一）董事会下设专门委员会成员情况｜V[1=]｜D0[年内召开董事会会议次数|16]｜D1[其中:现场会议次数|0]',
    '#140｜p142｜前:伙) 中国·上海 中国注册会计师：刘钰 (项目合伙人) 中国注册会计师：付文婷 2026年3月27日 141 长城汽车股份有限公司 2025年12月31日 合并资产负债表 人民币元｜后:142 长城汽车股份有限公司 2025年12月31日 合并资产负债表 - 续 人民币元｜H0[项目|附注|2025年12月31日(经审计)|2024年12月31日(已重述)]｜V[2=2025年12月31日(经审计),3=2024年12月31日(已重述)]｜D0[流动资产:|||]｜D1[货币资金|(六)1|28,846,312,373.34|30,768,672,688.70]',
    '#141｜p143｜前:142 长城汽车股份有限公司 2025年12月31日 合并资产负债表 - 续 人民币元｜后:附注为财务报表的组成部分 魏建军 法定代表人 李红栓 主管会计工作负责人 王海萍 会计机构负责人 143 长城汽车股份有限公司 2025年12月31日 公司资产负债表 人民币元｜H0[项目|附注|2025年12月31日(经审计)|2024年12月31日(已重述)]｜V[2=2025年12月31日(经审计),3=2024年12月31日(已重述)]｜D0[流动负债:|||]｜D1[短期借款|(六)20|6,531,885,229.35|6,684,584,370.91]',
    '#142｜p144｜前:附注为财务报表的组成部分 魏建军 法定代表人 李红栓 主管会计工作负责人 王海萍 会计机构负责人 143 长城汽车股份有限公司 2025年12月31日 公司资产负债表 人民币元｜后:144 长城汽车股份有限公司 2025年12月31日 公司资产负债表 - 续 人民币元｜H0[项目|附注|2025年12月31日(经审计)|2024年12月31日(经审计)]｜V[2=2025年12月31日(经审计),3=2024年12月31日(经审计)]｜D0[流动资产:|||]｜D1[货币资金|(十七)1|9,822,507,870.33|10,414,232,314.68]',
    '#144｜p146｜前:145 长城汽车股份有限公司 2025年12月31日止年度 合并利润表 人民币元｜后:注: 本年发生同一控制下企业合并的, 被合并方在合并前实现的净亏损为人民币 13,709,340.01 元, 上年被合并方实现的净亏损为人民币 30,475,641.99 元。 1｜H0[项目|附注|2025年度(经审计)|2024年度(已重述)]｜V[2=2025年度(经审计),3=2024年度(已重述)]｜D0[一、营业总收入||222,824,238,516.25|202,193,779,642.49]｜D1[其中:营业收入|(六)38|222,824,238,516.25|202,193,779,642.49]',
    '#148｜p150｜前:149 长城汽车股份有限公司 2025年12月31日止年度 合并股东权益变动表 人民币元｜后:150 长城汽车股份有限公司 2025年12月31日止年度 合并股东权益变动表 - 续 人民币元｜H0[项目|2025年度(经审计)|2025年度(经审计)|2025年度(经审计)|2025年度(经审计)|2025年度(经审计)|2025年度(经审计)|2025年度(经审计)]｜H1[项目|归属于母公司股东权益|归属于母公司股东权益|归属于母公司股东权益|归属于母公司股东权益|归属于母公司股东权益|归属于母公司股东权益|归属于母公司股东权益]｜H2[项目|股本|其他权益工具|资本公积|减:库存股|其他综合收益|专项储备|盈余公积]｜V[1=2025年度(经审计)/归属于母公司股东权益/股本,2=2025年度(经审计)/归属于母公司股东权益/其他权,3=2025年度(经审计)/归属于母公司股东权益/资本公,4=2025年度(经审计)/归属于母公司股东权益/减:库,5=2025年度(经审计)/归属于母公司股东权益/其他综,6=2025年度(经审计)/归属于母公司股东权益/专项储,7=2025年度(经审计)/归属于母公司股东权益/盈余公,8=2025年度(经审计)/归属于母公司股东权益/未分配,9=2025年度(经审计)/少数股东权益,10=2025年度(经审计)/股东权益合计]｜D0[一、上年年末余额|8,556,164,379.00|335,554,731.55|3,625,847,438.66|950,845,326.57|(1,299,163,945.22)|344,662,183.20|6,944,280,309.94]｜D1[加:同一控制下企业合并|-|-|40,000,000.00|-|-|-|-]',
    '#158｜p198｜前:扣进项税额加计 \\(5\\%\\) 抵减应纳增值税税额。 197 长城汽车股份有限公司 财务报表附注 2025年12月31日止年度 (六) 合并财务报表项目注释 1、货币资金 人民币元｜后:2025年12月31日，本集团使用受到限制的货币资金为人民币3,526,110,675.34元。其中银行承兑汇票保证金人民币3,137,454,185.91元；信用证保证金人民币1｜H0[项目|2025年12月31日(经审计)|2024年12月31日(已重述)]｜V[1=2025年12月31日(经审计),2=2024年12月31日(已重述)]｜D0[现金:||]｜D1[人民币|76,186.31|1,581,213.78]',
    '#161｜p199｜前:以上应收账款账龄分析是以收入确认的时间为基础。 (2) 按信用损失计提方法分类披露 人民币元｜后:按单项计提信用损失准备 人民币元｜H0[种类|2025年12月31日(经审计)|2025年12月31日(经审计)|2025年12月31日(经审计)|2025年12月31日(经审计)|2025年12月31日(经审计)|2024年12月31日(已重述)|2024年12月31日(已重述)]｜H1[种类|账面余额|账面余额|信用损失准备|信用损失准备|账面价值|账面余额|账面余额]｜H2[种类|金额|比例(%)|金额|计提比例(%)|账面价值|金额|比例(%)]｜V[1=2025年12月31日(经审计)/账面余额/金额,2=2025年12月31日(经审计)/账面余额/比例(%,3=2025年12月31日(经审计)/信用损失准备/金额,4=2025年12月31日(经审计)/信用损失准备/计提,5=2025年12月31日(经审计)/账面价值,6=2024年12月31日(已重述)/账面余额/金额,7=2024年12月31日(已重述)/账面余额/比例(%,8=2024年12月31日(已重述)/信用损失准备/金额,9=2024年12月31日(已重述)/信用损失准备/计提,10=2024年12月31日(已重述)/账面价值]｜D0[按单项计提信用损失|348,540,664.54|3.47|(348,540,664.54)|100.00|-|356,311,522.70|4.59]｜D1[按组合计提信用损失|9,696,750,041.79|96.53|(97,535,530.04)|1.01|9,599,214,511.75|7,406,881,514.29|95.41]',
    '#182｜p206｜前:团无涉及政府补助的其他应收款。 205 长城汽车股份有限公司 财务报表附注 2025年12月31日止年度 (六) 合并财务报表项目注释 - 续 7、存货 (1) 存货分类 人民币元｜后:人民币元｜H0[项目|2025年12月31日(经审计)|2025年12月31日(经审计)|2025年12月31日(经审计)]｜H1[项目|账面余额|跌价准备|账面价值]｜V[1=2025年12月31日(经审计)/账面余额,2=2025年12月31日(经审计)/跌价准备,3=2025年12月31日(经审计)/账面价值]｜D0[原材料|5,455,960,269.68|(132,261,666.29)|5,323,698,603.39]｜D1[在产品|1,823,663,805.67|(4,311,199.98)|1,819,352,605.69]',
    '#267｜p246｜前:245 长城汽车股份有限公司 财务报表附注 2025年12月31日止年度 (六) 合并财务报表项目注释 - 续 57、现金流量表补充资料 (1) 现金流量表补充资料 人民币元｜后:246 长城汽车股份有限公司 财务报表附注 2025年12月31日止年度 (六) 合并财务报表项目注释 - 续 57、现金流量表补充资料 - 续 (2) 现金和现金等价物的构成 人｜H0[补充资料|2025年度(经审计)|2024年度(已重述)]｜V[1=2025年度(经审计),2=2024年度(已重述)]｜D0[1.将净利润调节为经营活动现金流量:||]｜D1[净利润|9,865,280,190.16|12,660,012,515.78]',
    '#370｜p301｜前:300 长城汽车股份有限公司 财务报表附注 2025年12月31日止年度 (十七) 公司财务报表主要项目注释 - 续 24、现金流量表补充资料 (1) 现金流量表补充资料 人民币元｜后:301 长城汽车股份有限公司 财务报表附注 2025年12月31日止年度 (十七) 公司财务报表主要项目注释 - 续 24、现金流量表补充资料 - 续 (2) 现金和现金等价物的构｜H0[补充资料|2025年度(经审计)|2024年度(经审计)]｜V[1=2025年度(经审计),2=2024年度(经审计)]｜D0[1.将净利润调节为经营活动现金流量:||]｜D1[净利润|6,374,552,446.43|6,497,622,911.01]',
]

_ANNOTATE_DEMO = dspy.Example(
    digests="\n".join(_ANNOTATE_DEMO_DIGESTS),
    tables=[
        {"idx": 10, "category": "kpi", "title": "主要会计数据", "entity": "con", "unit": "万元", "currency": "CNY"},
        {"idx": 26, "category": "ops", "title": "产销量情况分析表", "entity": "con", "unit": "", "currency": ""},
        {"idx": 140, "category": "bs", "title": "合并资产负债表", "entity": "con", "unit": "元", "currency": "CNY"},
        {"idx": 141, "category": "bs", "title": "合并资产负债表", "entity": "con", "unit": "元", "currency": "CNY"},
        {"idx": 142, "category": "bs", "title": "公司资产负债表", "entity": "par", "unit": "元", "currency": "CNY"},
        {"idx": 144, "category": "is", "title": "合并利润表", "entity": "con", "unit": "元", "currency": "CNY"},
        {"idx": 148, "category": "se", "title": "合并股东权益变动表", "entity": "con", "unit": "元", "currency": "CNY"},
        {"idx": 158, "category": "note", "title": "货币资金", "entity": "con", "unit": "元", "currency": "CNY"},
        {"idx": 161, "category": "note", "title": "应收账款", "entity": "con", "unit": "元", "currency": "CNY"},
        {"idx": 182, "category": "note", "title": "存货", "entity": "con", "unit": "元", "currency": "CNY"},
        {"idx": 267, "category": "cfs", "title": "现金流量表补充资料", "entity": "con", "unit": "元", "currency": "CNY"},
        {"idx": 370, "category": "cfs", "title": "现金流量表补充资料", "entity": "par", "unit": "元", "currency": "CNY"},
    ],
).with_inputs("digests")


class TableAnnotator(dspy.Module):
    """含数值列的表 → 逐批语义标注。批与批互不影响：一次标全量会被 max_tokens 截断，
    整份年报的表一起丢。"""

    def __init__(self):
        super().__init__()
        self.annotate = dspy.Predict(AnnotateTables)
        self.annotate.demos = [_ANNOTATE_DEMO]

    def forward(self, tables: list[RawTable], shapes: list[Shape],
                source: str, alerts: list[str]) -> list[TableAnno]:
        cands = [i for i, sh in enumerate(shapes) if sh.value_cols]
        out: list[TableAnno] = []
        for pos in range(0, len(cands), _TABLE_BATCH):
            batch = cands[pos:pos + _TABLE_BATCH]
            digests = "\n".join(_digest(i, tables[i], shapes[i]) for i in batch)
            with tag(f"accounting:tables:{source}"):
                try:
                    annos = self.annotate(digests=digests,
                                          config=DETERMINISTIC
                                          | {"max_tokens": _TABLE_BATCH_OUT}).tables
                except Exception as e:  # adapter 解析失败等：本批不标注，其余批继续
                    alerts.append(f"表格标注 {source} 第 {pos // _TABLE_BATCH + 1} 批失败"
                                  f"（{e}），{len(batch)} 张表跳过")
                    continue
            out += self._checked(annos, batch, source, alerts)
        logger.info("表格标注 %s：%d/%d 张含数值表被标为财务表", source, len(out), len(cands))
        return out

    @staticmethod
    def _checked(annos, batch, source, alerts) -> list[TableAnno]:
        """机械防呆：类别/口径/列号不合法的一律丢弃或校正，不信任模型输出。"""
        ok = set(batch)
        out: list[TableAnno] = []
        for a in annos or []:
            if a.category not in CATEGORIES or not a.title.strip():
                continue
            if a.idx not in ok:      # 幻觉序号（本批没有的表）直接丢
                alerts.append(f"表格标注 {source} 出现本批之外的表号 #{a.idx}，丢弃")
                continue
            if a.entity not in ENTITIES:
                a.entity = "con"     # 口径认不清按合并处理（母公司表模型能分出来）
            a.title = a.title.strip()
            out.append(a)
        return out


# ---- 结构化表格：值列 × 定标 + 可验证性证据（证据由算术测得，不由模型声明） ----

VERIFY_TIED = "tied"        # 与主表同科目同期间勾稽上——受会计准则约束，两条取数路径互证
VERIFY_SUM = "sum"          # 表内合计恒等式成立——这张表自己声明的加总关系，程序算出来对得上
VERIFY_NONE = "unverified"  # 说明性表格：只有量纲与格式检查，没有会计勾稽可验（不得当已验证数字）


@dataclass
class FinRow:
    """一行：索引列原文（label 是最左一列，key 是全索引列拼接）。值按列存放。"""

    label: str
    key: str
    texts: list[str] = field(default_factory=list)


@dataclass
class FinColumn:
    """一个值列：列定义 + 与 rows 对齐的值向量 + 原文向量 + 可验证性证据。"""

    spec: ColSpec
    values: list[float | None] = field(default_factory=list)
    raws: list[str] = field(default_factory=list)
    verify: str = VERIFY_NONE
    total_row: int | None = None      # 合计行行号（由加总恒等式确认，不是看「合计」字样）


@dataclass
class FinTable:
    """一张财务表的结构化产物：rows × columns 的表，金额列单位亿，每列带可验证性标签。"""

    key: str                              # <公告文件>#<表序>，可回原文定位
    title: str                            # 模型给的表名（科目名）
    category: str                         # bs/is/cf/cfs/se/note/kpi/ops
    entity: str                           # con 合并 / par 母公司
    page: int | None                      # 印刷页码（引用出处）
    money: bool                           # 表内声明了金额单位 ⇒ 金额列已定标为亿
    unit: str = ""
    currency: str = ""
    before: str = ""                      # 表前原文（表题、附注编号、单位声明）
    after: str = ""                       # 表后原文（「注：…」限定条件在这儿，别丢）
    header: list[list[str]] = field(default_factory=list)
    rows: list[FinRow] = field(default_factory=list)
    columns: list[FinColumn] = field(default_factory=list)
    value_cells: int = 0                  # 值列里非空的单元格数（应解析的全部）
    parsed_cells: int = 0                 # 其中解析出值的（差额逐表披露，不静默丢数）

    @property
    def is_statement(self) -> bool:
        return self.category in STMT_KINDS

    def markdown(self, cols: list[int] | None = None,
                 rows: int = 40, values: bool = True) -> str:
        """渲染成 markdown 表（程序制表，模型不口算）：列名用表头路径，值列标注证据级别。

        只渲染值列 + 索引列；`values=False` 时打印原文（核对 OCR 用）。
        """
        picked = [c for c in self.columns if cols is None or c.spec.i in cols]
        head = "| 行 | " + " | ".join(f"{c.spec.label.replace('|', '／')}〔{c.verify}〕"
                                      for c in picked) + " |"
        title = (f"### {self.title}（{self.category}/{self.entity}，p{self.page}，"
                 + (f"单位 {self.unit}，值为亿）" if self.money else "非金额表）"))
        lines = [title, head, "|" + "---|" * (len(picked) + 1)]
        for k, r in enumerate(self.rows[:rows]):
            cells = []
            for c in picked:
                v = c.values[k] if k < len(c.values) else None
                cells.append("" if v is None else (f"{v:,.2f}" if values else c.raws[k]))
            lines.append(f"| {r.key[:36]} " + "".join(f"| {x} " for x in cells) + "|")
        if len(self.rows) > rows:
            lines.append(f"| …另 {len(self.rows) - rows} 行 |")
        return "\n".join(lines)

    def column(self, i: int) -> FinColumn | None:
        return next((c for c in self.columns if c.spec.i == i), None)

    def total(self, i: int) -> float | None:
        """某列的合计值（仅当该列合计行由加总恒等式确认时才有）。"""
        col = self.column(i)
        if not col or col.total_row is None:
            return None
        return col.values[col.total_row]


def _column_total(values: list[float | None], rows: list[FinRow]) -> tuple[int | None, int]:
    """一列的加总证据：返回（合计行行号或 None，成立的恒等式条数）。

    某行等于其前面若干行之和（`_keep_flags`）即聚合行——只用数值恒等式判断，不认
    「合计」「小计」字样：字样会漏写法也误伤同名明细。末行是聚合行 ⇒ 它就是合计行；
    任何聚合行被识出 ⇒ 这一列的行序、对齐、定标都对（提取正确性的正向证据）。
    """
    items = [(str(p), rows[p].key, v) for p, v in enumerate(values) if v is not None]
    if len(items) < 3:
        return None, 0
    keep = _keep_flags(items)
    found = sum(1 for k in keep if not k)
    return (int(items[-1][0]) if not keep[-1] else None), found


def _scaled(col: ColSpec, digits: float, scale: float | None) -> float:
    """归一值：比率去掉百分号；金额列按表内单位声明定标成亿；计数原样。

    表内没声明金额单位 ⇒ 它不是金额表（「单位：台/辆/股」与漏印声明都算），形态像金额的
    列按计数处理：原值入表、不定标。丢数不如带着量纲标注交给下游，但绝不允许冒充亿口径。
    """
    if col.kind == "ratio":
        return digits / 100
    if col.kind == "amount" and scale is not None:
        return digits * scale
    return digits


def build_fin_tables(tables: list[RawTable], shapes: list[Shape], annos: list[TableAnno],
                     alerts: list[str], source: str = "") -> list[FinTable]:
    """标注 + 摊平结构 → 财务表格集（纯程序）。

    续页拼接：相邻、表头块逐字相同、且模型认定同一张表（表名/类别/口径全同）的表是同一张
    表的续页。判据不齐即停——宁可留两张半表让加总校验报不出合计行，也不把两张不同的表糊成
    一张（短期借款与长期借款表头逐字相同，只有表名分得开它们）。
    """
    by_idx = {a.idx: a for a in annos if 0 <= a.idx < len(tables) and a.title}
    seen: set[int] = set()
    out: list[FinTable] = []
    for i, t in enumerate(tables):
        a = by_idx.get(i)
        if a is None or i in seen:
            continue
        frags, end = [i], i + 1
        while end < len(tables) and (b := by_idx.get(end)) is not None \
                and (b.title, b.category, b.entity) == (a.title, a.category, a.entity) \
                and shapes[end].header == shapes[i].header \
                and len(shapes[end].cols) == len(shapes[i].cols):
            frags.append(end)
            seen.add(end)
            end += 1
        ft = _assemble(f"{source}#{i}", a, [tables[k] for k in frags],
                       [shapes[k] for k in frags], alerts)
        if not ft.rows:
            alerts.append(f"表格 {ft.key}「{a.title}」标为 {a.category}，但没有可解析的数据行")
            continue
        out.append(ft)
    return out


def _cell(r: list[str], i: int) -> str:
    """第 i 格的原文（越界或缺失一律空串——摊平后仍可能有行短于表宽）。"""
    return r[i].strip() if i < len(r) and r[i] else ""


def _assemble(key: str, a: TableAnno, frags: list[RawTable], shapes: list[Shape],
              alerts: list[str]) -> FinTable:
    """一张表（含续页片段）→ FinTable：定标、逐格取值、加总证据、单元格审计。"""
    scale = scale_of_unit(a.unit)
    money = scale is not None
    sh = shapes[0]
    ft = FinTable(key=key, title=a.title, category=a.category, entity=a.entity,
                  page=frags[0].page, money=money, unit=a.unit, currency=a.currency,
                  before=frags[0].before, after=max((f.after for f in frags), key=len),
                  header=sh.header)
    keys = [c.i for c in sh.cols if not c.is_value]
    ft.rows = [FinRow(label=_cell(r, 0),
                      key=" ".join(x for x in (_cell(r, i) for i in keys) if x),
                      texts=[x for x in (_cell(r, i) for i in keys) if x])
               for frag, s in zip(frags, shapes, strict=True)
               for r in frag.rows[len(s.header):]]
    unparseable: list[str] = []
    for c in sh.cols:
        if not c.is_value:
            continue
        raws, values = [], []
        for frag, s in zip(frags, shapes, strict=True):
            for r in frag.rows[len(s.header):]:
                raw = _cell(r, c.i)
                if _blank(raw):
                    raw, parsed = "", None
                else:
                    ft.value_cells += 1
                    parsed = parse_number(raw)
                if parsed and raw:
                    ft.parsed_cells += 1
                    values.append(_scaled(c, parsed[0], scale))
                elif raw:
                    values.append(None)
                    unparseable.append(f"{ft.rows[len(values) - 1].key}｜{raw}")
                else:
                    values.append(None)
                raws.append(raw)
        total, found = _column_total(values, ft.rows)
        ft.columns.append(FinColumn(spec=c, values=values, raws=raws, total_row=total,
                                    verify=VERIFY_SUM if found else VERIFY_NONE))
    if unparseable:
        alerts.append(f"表格 {key}「{a.title}」{len(unparseable)}/{ft.value_cells} 格带数字却读不出"
                      f"（OCR 粘连/错读，弃该格）：{'；'.join(unparseable[:3])}")
    return ft


def _keep_flags(items: list[tuple[str, str, float]], tol: float = 0.005) -> list[bool]:
    """按原文行序标出聚合行：某行的值等于其前面若干行之和（tol 内）即小计/总计。

    只用加总恒等式判断，不匹配科目名——「合计」「总计」字样既漏写法（「流动资产小计」）
    也误伤同名明细。三条约束压住误判：成员至少两项、每项都严格小于聚合行、累加一旦越过
    目标值即停（巧合凑数多半发生在跨过大量无关行之后）。

    从紧邻的前一行往前累加并跳过本轮已判为聚合的行——「资产总计 = 流动资产合计 + 非流动
    资产合计」这种成员本身是小计、又被明细隔开的写法，一遍就识别成「资产总计 = 全部资产
    明细之和」，不需要多轮。
    """
    keep = [True] * len(items)
    for pos, (_k, _d, vi) in enumerate(items):
        if vi == 0:
            continue
        run, members = 0.0, 0
        for j in range(pos - 1, -1, -1):
            if not keep[j]:
                continue                      # 已判为聚合行，不当成员
            v = items[j][2]
            if abs(v) >= abs(vi):
                break                         # 成员必须都严格小于聚合行
            run += v
            members += 1
            if abs(run - vi) <= abs(vi) * tol:
                if members >= 2:
                    keep[pos] = False
                break
            if (vi > 0 and run > vi) or (vi < 0 and run < vi):
                break                         # 越过目标值，再往前只会更远
    return keep


def _drop_aggregates(items: list[tuple[str, str, float]],
                     tol: float = 0.005) -> tuple[list[tuple[str, str, float]], list[str]]:
    """`_keep_flags` 的清单视图：返回（明细行, 被剔除的聚合行展示名）。"""
    keep = _keep_flags(items, tol)
    return ([it for it, k in zip(items, keep, strict=True) if k],
            [it[1] for it, k in zip(items, keep, strict=True) if not k])


# ---- 概念数字表：主表（合并口径）→ {概念: {年份: 亿}}，估值与装配从这里取数 ----

CONCEPTS = [
    "revenue", "gross_profit", "gross_margin", "operating_profit", "pre_tax_profit",
    "net_profit", "total_assets", "total_liabilities", "total_equity",
    "current_liabilities", "operating_cash_flow", "capex", "cash_and_equivalents",
    "short_term_debt", "long_term_debt", "bonds_payable", "lease_liabilities",
    "depreciation_amortization", "dividends_paid",
]
_BALANCE_TRIPLE = ("total_assets", "total_liabilities", "total_equity")
_DEBT_PARTS = ("short_term_debt", "long_term_debt", "bonds_payable", "lease_liabilities")
_LABEL_BATCH = 120   # 每批归一的科目数（JSON 输出约 4-6k 字符，留足 max_tokens 余量）


class MapLabels(dspy.Signature):
    """把誊录的报表科目名映射到标准概念；不属于任何一个的原样保留科目名。

    口径必须分清：total_equity 只指**含少数股东**的「股东权益（所有者权益）合计」，
    「归属于母公司股东权益合计」是另一个口径（差额就是少数股东权益），原样保留不要映射；
    同理 revenue 是「营业总收入」而非「其中：营业收入」，net_profit 是「净利润」而非
    「归属于母公司股东的净利润」。附注表名与被注释的报表科目同名时才映射到同一概念。
    """

    labels: str = dspy.InputField(desc="科目名清单，每行一个")
    mapping: dict[str, str] = dspy.OutputField(
        desc='{"科目名": "概念或原样"}，键逐字取自输入清单。标准概念：' + ", ".join(CONCEPTS))


_MAP_DEMO = dspy.Example(
    labels="營業收入\n其中：主營業務收入\n所有者权益合计\n归属于母公司所有者权益合计\n负债合计\n"
           "负债和股东权益总计\n經營活動產生的現金流量淨額\n籌資活動(使用)產生的現金流量淨額\n"
           "净利润\n归属于母公司股东的净利润\n"
           "购建固定资产、无形资产和其他长期资产支付的现金\n"
           "購建固定資產、無形資產和其他長期資產支付的現金\n支付其他与筹资活动有关的现金\n短期借款",
    mapping={"營業收入": "revenue", "其中：主營業務收入": "其中：主營業務收入",
             "所有者权益合计": "total_equity",
             "归属于母公司所有者权益合计": "归属于母公司所有者权益合计",
             "负债合计": "total_liabilities", "负债和股东权益总计": "负债和股东权益总计",
             "經營活動產生的現金流量淨額": "operating_cash_flow",
             "籌資活動(使用)產生的現金流量淨額": "籌資活動(使用)產生的現金流量淨額",
             "净利润": "net_profit", "归属于母公司股东的净利润": "归属于母公司股东的净利润",
             "购建固定资产、无形资产和其他长期资产支付的现金": "capex",
             "購建固定資產、無形資產和其他長期資產支付的現金": "capex",
             "支付其他与筹资活动有关的现金": "支付其他与筹资活动有关的现金",
             "短期借款": "short_term_debt"},
).with_inputs("labels")


def statement_rows(fins: list[FinTable], alerts: list[str]) -> list[dict]:
    """合并口径主表 → 概念行 [{label, year, raw, value, kind}]。

    年份取列头印着的年份（`ColSpec.year`，由 colspan 摊平后精确归属），不靠「公告年份-1」
    推断；列头没有年份的主表列不用并披露——猜一个年份会把数字挂错期。
    """
    rows: list[dict] = []
    seen: dict[tuple[str, str], str] = {}
    for ft in fins:
        if not ft.is_statement or ft.entity != "con":
            continue
        if not any(c.spec.year for c in ft.columns):
            alerts.append(f"数字表 {ft.key}「{ft.title}」列头没有年份，本表不取数")
            continue
        for col in ft.columns:
            if not col.spec.year:
                continue
            for row, raw, v in zip(ft.rows, col.raws, col.values, strict=True):
                if not row.label or not raw:
                    continue
                key = (row.label, col.spec.year)
                if key in seen:
                    if seen[key] != raw:
                        alerts.append(f"数字表行冲突 {key[0]} {key[1]}年：「{seen[key]}」vs"
                                      f"「{raw}」（同名科目跨表/串行，保留先出）")
                    continue
                seen[key] = raw
                if v is not None:
                    rows.append({"label": row.label, "year": col.spec.year,
                                 "raw": raw, "value": v, "kind": ft.category})
    return rows


def merged_values(extracts: list[tuple[int, list[dict]]], concept_of: dict[str, str],
                  alerts: list[str]) -> dict[str, dict[str, float]]:
    """行 → {key: {年份: 亿}}。冲突只披露不修复：同份公告保留先出（主表先于附注、
    主科目先于衍生合计）；跨公告取最新（重述语义）。"""
    latest: dict[tuple[str, str], tuple[int, float, str]] = {}
    for idx, rows in extracts:
        for r in rows:
            key = concept_of.get(r["label"], r["label"])
            cell = (key, r["year"])
            if cell in latest:
                pi, pv, praw = latest[cell]
                if not same_value(pv, r["value"]):
                    alerts.append(f"数字表冲突 {key} {cell[1]}年：公告{pi}「{praw}」"
                                  f"{'≠' if pi == idx else '← 取最新'} 公告{idx}「{r['raw']}」")
                if pi == idx:
                    continue
            latest[cell] = (idx, r["value"], r["raw"])
    out: dict[str, dict[str, float]] = {}
    for (key, y), (_i, v, _r) in latest.items():
        out.setdefault(key, {})[y] = v
    return out


def tie_to_statements(fins: list[FinTable], merged: dict, concept_of: dict[str, str],
                      alerts: list[str]) -> int:
    """给每列打可验证性标签：与主表同科目同期间勾稽上 ⇒ tied（覆盖 sum 标签）。

    不等**不算异常**：同一科目在附注里可以有毛额/净额、期初/期末、账面余额/减值准备等
    不同口径（实测 19 处「不符」全是这类合法差异），逐条报会把 meta 变成噪声；只汇总一条，
    逐条进日志。相等才升格为 tied——这才是可复现的正向证据。
    """
    tied, mismatch = 0, []
    for ft in fins:
        if ft.is_statement or ft.entity != "con" or not ft.money:
            continue
        series = merged.get(concept_of.get(ft.title, ft.title)) or {}
        if not series:
            continue
        for col in ft.columns:
            y, total = col.spec.year, col.total_row
            v = col.values[total] if total is not None else None
            if not y or v is None or y not in series:
                continue
            if same_value(v, series[y]):
                col.verify = VERIFY_TIED
                tied += 1
            else:
                mismatch.append(f"{ft.title} {y}年 {v:.4g}≠{series[y]:.4g}")
    if mismatch:
        logger.warning("附注合计与主表不同口径（不视为异常）：%s", "；".join(mismatch))
        alerts.append(f"附注合计与主表同科目不等的列 {len(mismatch)} 个（毛额/净额、期初/期末、"
                      f"减值准备等口径差异，不计为异常），勾稽一致 {tied} 列；例：{'；'.join(mismatch[:3])}")
    logger.info("附注↔主表勾稽：%d 列 tied", tied)
    return tied


def identity_alerts(merged: dict) -> list[str]:
    """资产=负债+权益 逐年检错（零语义机械规则）：残差超 1% 披露告警，不修复。"""
    series = {m: merged.get(m, {}) for m in _BALANCE_TRIPLE}
    out = []
    for y in sorted({y for s in series.values() for y in s}):
        a, liab, eq = (series[m].get(y) for m in _BALANCE_TRIPLE)
        if a is None or liab is None or eq is None:
            continue
        res = a - liab - eq
        if abs(res) > max(abs(a) * 0.01, 1e-6):
            out.append(f"{y}年 资产{a:.2f} ≠ 负债{liab:.2f}+权益{eq:.2f}（残差{res:+.2f}亿）")
    for msg in out:
        logger.warning("数字表恒等式不满足：%s", msg)
    return out


@dataclass
class AccountingTable:
    """概念数字表 + 财务表格集：数字的唯一出处。values 键为标准概念，extra 键为原文科目。

    tables 是同一次直解产出的结构化财务表格集（主表 + 附注 + 指标 + 经营表）；概念表
    只有主表口径的锚点，附注明细（账龄、存货、减值、费用构成…）在 tables 里，每列带
    可验证性标签（tied/sum/unverified）。
    """

    values: dict[str, dict[str, float]]      # concept -> 年份 -> 亿
    extra: dict[str, dict[str, float]]       # 原文科目 -> 年份 -> 亿
    currency: str = ""
    years: list[str] = field(default_factory=list)
    identity_alerts: list[str] = field(default_factory=list)   # 恒等式检错披露（不修复）
    stage_alerts: list[str] = field(default_factory=list)     # 标注/冲突/弃格检出，随 meta 交付
    label_names: dict[str, str] = field(default_factory=dict)  # concept -> 首个原文科目名
    order: list[str] = field(default_factory=list)   # 最新公告的科目原文行序（聚合行识别用）
    kinds: dict[str, str] = field(default_factory=dict)   # 科目 -> 来源主表
    tables: list[FinTable] = field(default_factory=list)

    def asset_line_items(self) -> list[str]:
        """资产负债表资产段的科目键（原文行序）。

        按结构划界而非科目名：利润表与现金流量表的大额行（营业总收入、现金流入小计、
        期末现金及现金等价物余额）都不是资产，混进清算清单会让合计超出资产总计数倍；
        A股/港股资产负债表把资产段整体排在「资产总计」之前，用它划界即可。认不出来源表
        时退回全量行序，由清算环节的上界对账兜底——宁可判不适用，不产出虚高数字。
        """
        bs = [k for k in self.order if self.kinds.get(k) == "bs"]
        if not bs:
            return list(self.order)
        if "total_assets" in bs:
            return bs[:bs.index("total_assets")] or bs
        return bs

    def latest_label_values(self) -> tuple[list[tuple[str, str, float]], list[str]]:
        """最新年份的资产明细科目（原文行序→按绝对值降序），供清算折扣清单。

        三道机械处理，只用结构与数值恒等式、不看科目名：取资产段行序；剔除聚合行
        （某行等于其前面若干行之和即小计/总计，与明细一起进清单会重复计入，实测能把
        清算价值虚高数倍——这是正常运作，只记日志）；剔除账面值超过资产总计的项
        （定标可疑，披露）。留哪些科目是清算环节的判断，不在这里。
        """
        if not self.years:
            return [], []
        y = self.years[-1]
        cap = self.values.get("total_assets", {}).get(y)

        def value_of(key: str) -> float | None:
            return (self.values.get(key) or self.extra.get(key) or {}).get(y)

        ordered = [(k, self.label_names.get(k, k), v)
                   for k in self.asset_line_items() if (v := value_of(k)) is not None]
        leaves, aggregates = _drop_aggregates(ordered)
        if aggregates:
            logger.info("清算清单剔除 %d 个聚合行（小计/总计）：%s",
                        len(aggregates), "、".join(aggregates[:8]))
        alerts = []
        rows = [(k, d, v) for k, d, v in leaves if not (cap and abs(v) > cap * 1.05)]
        if (over := len(leaves) - len(rows)):
            alerts.append(f"清算清单剔除 {over} 项超过资产总计的异常值（定标可疑）")
        rows.sort(key=lambda kv: -abs(kv[2]))
        return rows, alerts


class AccountingBuilder(dspy.Module):
    """直解 → 表级标注 → 程序拼表取数 → 科目归一 → 程序合并（数字经代码处理，纯内存）。"""

    def __init__(self):
        super().__init__()
        self.annotate = TableAnnotator()
        self.map_labels = dspy.Predict(MapLabels)
        self.map_labels.demos = [_MAP_DEMO]

    def forward(self, derived: Path, filings: list[dict]) -> AccountingTable | None:
        """逐份公告提取 → 概念数字表 + 财务表格集。主表一行都提不出来即本环节失败
        （返回 None），不允许带着空锚点往下游走。"""
        extracts: list[tuple[int, list[dict]]] = []
        fins: list[FinTable] = []
        currencies: list[str] = []
        alerts: list[str] = []
        for i, f in enumerate(filings, 1):
            text = (derived / f["file"]).read_text(encoding="utf-8")
            tables = extract_tables(text)
            shapes = [shape_of(t) for t in tables]
            annos = self.annotate(tables, shapes, f["file"], alerts)
            per = build_fin_tables(tables, shapes, annos, alerts, source=f["file"])
            rows = statement_rows(per, alerts)
            fins += per
            currencies += [ft.currency for ft in per if ft.currency]
            logger.info("数字表 %s：直解 %d 张（含数值 %d）→ 财务表 %d 张 → 主表概念行 %d",
                        f["file"], len(tables), sum(1 for s in shapes if s.value_cols),
                        len(per), len(rows))
            if rows:
                extracts.append((i, rows))
        if not extracts:
            return None

        labels = (sorted({r["label"] for _, rows in extracts for r in rows}
                         | {ft.title for ft in fins if ft.category in ("note", "kpi")}))
        concept_of = self._canonicalize(labels, alerts)
        merged = merged_values(extracts, concept_of, alerts)
        tie_to_statements(fins, merged, concept_of, alerts)
        identity = identity_alerts(merged)

        # 资本开支是「购建固定资产…支付的现金」（原文括号负数口径），统一取绝对值
        if "capex" in merged:
            merged["capex"] = {y: abs(v) for y, v in merged["capex"].items()}
        # 有息负债 = 程序加总各组成部分（不让模型口算）
        debt: dict[str, float] = {}
        for y in sorted({y for m in _DEBT_PARTS for y in merged.get(m, {})}):
            if merged.get("short_term_debt", {}).get(y) is None \
                    and merged.get("long_term_debt", {}).get(y) is None:
                continue
            debt[y] = sum(merged.get(m, {}).get(y) or 0 for m in _DEBT_PARTS)
        if debt:
            merged["interest_bearing_debt"] = debt

        values = {k: v for k, v in merged.items() if k in CONCEPTS or k == "interest_bearing_debt"}
        extra = {k: v for k, v in merged.items() if k not in values}
        label_names: dict[str, str] = {}
        for lb, c in concept_of.items():
            if c != lb and c not in label_names:
                label_names[c] = lb
        order: list[str] = []
        kinds: dict[str, str] = {}
        for r in extracts[-1][1]:      # 最新一份公告的行序与来源表：聚合行识别与资产段划界靠它们
            key = concept_of.get(r["label"], r["label"])
            if key not in order:
                order.append(key)
                kinds[key] = r["kind"]
        return AccountingTable(
            values=values, extra=extra,
            currency=max(set(currencies), key=currencies.count) if currencies else "",
            years=sorted({y for s in merged.values() for y in s}), identity_alerts=identity,
            stage_alerts=alerts, label_names=label_names, order=order, kinds=kinds, tables=fins)

    def _canonicalize(self, labels: list[str], alerts: list[str]) -> dict[str, str]:
        """原文科目名 → 标准概念（LLM 语义归一），按批处理。映射值不是合法概念的一律按
        原样处理（机械防呆，不设复核环节）。"""
        out: dict[str, str] = {}
        for i in range(0, len(labels), _LABEL_BATCH):
            batch = labels[i:i + _LABEL_BATCH]
            with tag("accounting:labels"):
                try:
                    mapping = self.map_labels(labels="\n".join(batch),
                                              config=DETERMINISTIC | {"max_tokens": 8000}).mapping
                except Exception as e:   # adapter 解析失败等：本批原名兜底
                    alerts.append(f"科目归一第 {i // _LABEL_BATCH + 1} 批失败（{e}），"
                                  f"{len(batch)} 个科目保留原名")
                    mapping = None
            if not isinstance(mapping, dict):
                out.update({lb: lb for lb in batch})
                continue
            out.update({lb: c if (c := str(mapping.get(lb) or lb)) in CONCEPTS else lb
                        for lb in batch})
        logger.info("数字表科目归一：%d 个原文科目 → %d 个标准概念",
                    len(labels), len(set(out.values()) & set(CONCEPTS)))
        return out
