"""叙述节：一个 section 一个文件，各自都能单独执行。

    uv run -m cagent.narratives.business --stock 601633 --market cn
    uv run -m cagent.narratives.decisions --stock 601633 --market cn
"""

from cagent.narratives.business import BUSINESS
from cagent.narratives.decisions import DECISIONS

SECTIONS = (BUSINESS, DECISIONS)

__all__ = ["BUSINESS", "DECISIONS", "SECTIONS"]
