"""示例插件：数一数文本有多少字。

启用方式：跟 Agent 说「启用 word_count 插件」，它会让你确认一次；或手工在
data/plugins.json 的 enabled 里加 "word_count" 后重载。
"""

PLUGIN = {"name": "word_count", "description": "统计文本的字符数、中文汉字数、行数"}


def word_count(ctx, args):
    text = str(args.get("text") or "")
    chars = len(text)
    han = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    lines = len(text.splitlines()) or 1
    return f"字符 {chars} · 汉字 {han} · 行 {lines}"


def register(registry):
    registry.register(
        "word_count",
        "统计一段文本的字符数 / 汉字数 / 行数",
        {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "要统计的文本"}},
            "required": ["text"],
        },
        word_count,
    )
