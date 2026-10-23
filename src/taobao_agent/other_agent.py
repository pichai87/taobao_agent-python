"""A small local response for the article's greeting and fallback expert."""


def answer_other(question: str) -> str:
    """Reply to a greeting, or ask for a clearer analysis question."""

    normalized = question.strip().strip("！!。.?？ ").lower()
    if normalized in {"你好", "您好", "嗨", "hi", "hello"}:
        return "你好！你可以告诉我想分析的电商指标和时间范围。当前尚未连接业务数据。"
    return "请说明想查询的电商指标和时间范围，或想分析的变化。当前尚未连接业务数据。"
