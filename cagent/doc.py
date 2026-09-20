import re
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
PAGE_RE = re.compile(r"<!--\s*page\s+(\d+)\s*-->")
CJK_RE = re.compile(r"[一-鿿]")


def estimate_tokens(text: str) -> int:
    """粗估 token 数：CJK 每字 1 token，其余每 4 字符 1 token（不依赖分词器）。"""
    cjk = len(CJK_RE.findall(text))
    other = len(text) - cjk
    return cjk + (other + 3) // 4


@dataclass
class Section:
    """一段 markdown 文本，带起始页码。"""

    titles: list[str]
    text: str
    page_range: tuple[int, int]  # 起始页码，闭区间


class AnnualReport:
    def __init__(self, md_path: Path | str):
        self.path = Path(md_path)
        self.text = self.path.read_text(encoding="utf-8")
        self._page_marks = [(m.start(), int(m.group(1))) for m in PAGE_RE.finditer(self.text)]

    def _page_at(self, pos: int) -> int:
        """pos 之前最近一个 `<!-- page N -->` 的页码，没有则 1。"""
        i = bisect_right(self._page_marks, (pos, 10**9)) - 1
        return self._page_marks[i][1] if i >= 0 else 1

    def section_chunks(self) -> list[Section]:
        """按标题切分"""
        matches = list(HEADING_RE.finditer(self.text))
        spans = [(0, matches[0].start())] if matches and matches[0].start() > 0 else []
        spans += [
            (
                m.start(),
                matches[i + 1].start() if i + 1 < len(matches) else len(self.text),
            )
            for i, m in enumerate(matches)
        ]

        return [
            Section(
                [m.group(2) for m in matches if m.start() == a],
                self.text[a:b].strip(),
                (self._page_at(a), self._page_at(b)),
            )
            for a, b in spans
            if self.text[a:b].strip()
        ]

    def section_chunks_aggregate_max_token(self, max_tokens: int = 10000) -> list[Section]:
        """按标题切分后，合并相邻段落，直到 token 数超过 max_tokens。"""
        chunks = self.section_chunks()
        if not chunks:
            return []
        result = []
        current_titles = chunks[0].titles
        current_text = chunks[0].text
        current_page_start = chunks[0].page_range[0]
        current_page_end = chunks[0].page_range[1]
        for chunk in chunks[1:]:
            new_text = current_text + "\n\n" + chunk.text
            if estimate_tokens(new_text) > max_tokens:
                result.append(
                    Section(
                        current_titles,
                        current_text,
                        (current_page_start, current_page_end),
                    )
                )
                current_titles = chunk.titles
                current_text = chunk.text
                current_page_start = chunk.page_range[0]
                current_page_end = chunk.page_range[1]
            else:
                current_titles = current_titles + chunk.titles
                current_text = new_text
                current_page_end = chunk.page_range[1]
        result.append(Section(current_titles, current_text, (current_page_start, current_page_end)))
        return result


if __name__ == "__main__":
    report = AnnualReport(Path(__file__).parent / "test_annual.md")
    for section in report.section_chunks():
        print(section.titles)

    print("---" * 10)

    for section in report.section_chunks_aggregate_max_token(max_tokens=1000):
        print(section.titles)
