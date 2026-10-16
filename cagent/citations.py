"""citations：把出处从正文里收起来

同一份内容两种呈现：**正文给读者看，出处给核对用**。

生成时照旧要求标注（那是生成的纪律：写每个结论都要先找到依据），交付时按这里收起来：

    report.md        干净正文，读起来不被打断
    sources.md       出处对照：哪一节引了哪些出处
    各小节原始文件     保留完整标注（要核对时直接翻 q19.md / business.md）
    --with-sources   把标注放回正文（要逐条核对时用）

用法：
    from cagent.citations import split_citations
    clean, sources = split_citations(text)
"""

import re

# 括号里允许嵌一层括号：「（来源：每 100 元收入去向（元））」
_INNER = r"(?:[^（）]|（[^）]*）)*"
_PATTERNS = (
    # 页码写法很活：p.46-48、p.19, p.24-25、p.20-21、p.27
    re.compile(r"（见年报\s*(?:p\.?\s*\d+(?:\s*[-–—~]\s*\d+)?\s*[、,，]?\s*)+）"),
    re.compile(rf"（来源[:：]{_INNER}）"),
    re.compile(rf"（course\.md{_INNER}）"),
    re.compile(r"（见附注[^）]*）"),
)
_ORPHAN_SPACE = re.compile(r"[ \t]+(?=[，。；、）])")


def split_citations(text: str) -> tuple[str, list[str]]:
    """正文与出处分开：返回（去掉标注的正文, 按出现顺序的出处列表）。"""
    found: list[str] = []
    clean = text
    for pattern in _PATTERNS:
        found += [match.group(0)[1:-1].strip() for match in pattern.finditer(clean)]
        clean = pattern.sub("", clean)
    clean = _ORPHAN_SPACE.sub("", clean)
    clean = re.sub(r"\n{3,}", "\n\n", clean)
    clean = re.sub(r"[ \t]+\n", "\n", clean)
    return clean, found


def strip(text: str) -> str:
    """只要干净正文。"""
    return split_citations(text)[0]


def render_sources(sections: list[tuple[str, list[str]]]) -> str:
    """出处对照文件：[(小节标题, 出处列表)] → markdown。"""
    lines = ["# 出处对照", "", "报告正文默认不显示出处（保持阅读顺畅）；这里是逐节对照，核对时用。", ""]
    for title, sources in sections:
        if not sources:
            continue
        lines.append(f"## {title}")
        lines.append("")
        seen: list[str] = []
        for item in sources:
            if item not in seen:
                seen.append(item)
        lines += [f"- {item}" for item in seen]
        lines.append("")
    return "\n".join(lines).strip() + "\n"
