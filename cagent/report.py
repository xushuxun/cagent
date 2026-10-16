"""report：报告装配

把三类东西装成一份公司档案，顺序就是阅读顺序：

    一、生意模式        读年报原文，关键投入引财务证据（sections）
    二、历年经营决策    逐年读年报原文（sections）
    三、资本回报       14 个问题，每问一次生成、数字全部来自证据表（questions）
    四、数据           程序直解与算术的证据表 + 口径与来源（finance_series）

每类都先落成独立小节文件（可单独重跑），再合成 `report.md` 与 `index.html`。
装配只做拼装与校验：不碰数字、不改正文。

用法：
    uv run cagent/report.py --stock 601633 --market cn
    uv run cagent/report.py --stock 601633 --market cn --force      # 重跑全部小节
"""

import argparse
import logging
import sys
from pathlib import Path

from cagent.finance_series import evidence, load_series
from cagent.llm import LLM
from cagent.questions import QUESTIONS, answer
from cagent.sections import SECTIONS, write_section
from cagent.wiki_render import render_doc

logger = logging.getLogger(__name__)

_STRUCTURE = ("# ", "## 一、生意模式", "## 二、历年经营决策", "## 三、资本回报", "## 四、数据")


def _demote(text: str, target: int = 3) -> str:
    """把一小节的标题层级整体下移，使它的最高级标题落在 target 级——装配只调层级，不改正文。"""
    lines = text.splitlines()
    levels = [len(line) - len(line.lstrip("#")) for line in lines if line.startswith("#")]
    if not levels:
        return text
    shift = target - min(levels)
    out = []
    for line in lines:
        if line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            out.append("#" * max(1, level + shift) + " " + line.lstrip("# ").strip())
        else:
            out.append(line)
    return "\n".join(out)


def _wrap(title: str, text: str, *, drop_first_heading: bool = True, target: int = 3) -> str:
    """套上编号标题，并把它自己的最高级标题去掉（避免与编号标题重复）。"""
    lines = text.strip().splitlines()
    if drop_first_heading and lines and lines[0].startswith("# "):
        lines = lines[1:]
    body = _demote("\n".join(lines).strip(), target=target)
    return f"## {title}\n\n{body}".strip()


def _section_path(output_dir: Path, name: str) -> Path:
    return output_dir / f"{name}.md"


def _reuse_or_write(output_dir: Path, name: str, force: bool, build) -> str:
    path = _section_path(output_dir, name)
    if path.exists() and not force:
        logger.info(f"复用: {path.name}")
        return path.read_text(encoding="utf-8")
    logger.info(f"=== {name} ===")
    text = build()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return text


def build_report(agent: LLM, stock: str, market: str, root: Path, output_dir: Path, force: bool = False) -> Path:
    """合成一份档案，返回 report.md 路径。"""
    series = load_series(root, market, stock)
    years = sorted(series)
    evidence_text = evidence(series, years)

    parts: list[str] = []

    # 一、生意模式：读原文 + 引证据（引的数字受校验）
    business = _reuse_or_write(output_dir, "business", force, lambda: write_section(agent, SECTIONS[0], stock, market, root, evidence_text))
    parts.append(_wrap("一、生意模式", business))
    # 二、历年经营决策：逐年读原文
    decisions = _reuse_or_write(output_dir, "decisions", force, lambda: write_section(agent, SECTIONS[1], stock, market, root))
    parts.append(_wrap("二、历年经营决策", decisions))

    # 三、资本回报：14 问（标题编号即阅读顺序，判据紧跟在标题下）
    answers = ["## 三、资本回报"]
    for number, question in enumerate(QUESTIONS, start=1):
        body = _reuse_or_write(
            output_dir,
            question.id,
            force,
            lambda question=question: f"## {question.title}\n\n> 判据：{question.judgment}\n\n{answer(agent, series, years, question)[0]}\n",
        )
        lines = body.strip().splitlines()
        if lines and lines[0].startswith("#"):
            lines = lines[1:]
        answers.append(f"### {number}. {question.title}\n\n" + "\n".join(lines).strip())
    parts.append("\n\n".join(answers))

    # 四、数据：证据表与来源
    parts.append("## 四、数据\n\n" + evidence_text.split("\n", 1)[1].strip())

    doc = f"# {stock} · 公司档案（{market}）\n\n" + "\n\n".join(parts) + "\n"
    md_path = output_dir / "report.md"
    md_path.write_text(doc, encoding="utf-8")
    html_path = output_dir / "index.html"
    html_path.write_text(render_doc([doc], title=f"{stock}.{market} · 公司档案", ticker=stock), encoding="utf-8")
    logger.info(f"装配: {md_path}（{len(doc)} 字符）与 {html_path}")
    return md_path


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S", stream=sys.stderr)
    parser = argparse.ArgumentParser(description="report：装配公司档案")
    parser.add_argument("--stock", default="601633")
    parser.add_argument("--market", default="cn", choices=["cn", "hk"])
    parser.add_argument("--root", type=Path, default=Path(".cagent"))
    parser.add_argument("--out", type=Path, default=Path("output/wiki"))
    parser.add_argument("--force", action="store_true", help="重跑全部小节")
    args = parser.parse_args()

    agent = LLM(trace=True)
    output_dir = args.out / args.market / args.stock
    output_dir.mkdir(parents=True, exist_ok=True)
    build_report(agent, args.stock, args.market, args.root, output_dir, force=args.force)
    print(agent.ledger.summary())


if __name__ == "__main__":
    main()
