"""知识层文档格式：writer 输出自由 Markdown 蒸馏文档（doc），附章节页码定位索引。

结构随公司而变，不设固定块类型；reader 只做 Markdown→HTML 格式转换。
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "updated": {"type": "boolean"},
        "doc": {"type": "string"},
    },
    "required": ["updated", "doc"],
}
