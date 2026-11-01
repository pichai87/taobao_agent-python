"""A small local response for the article's greeting and fallback expert."""

from taobao_agent.agents.harness import RunHarness
from taobao_agent.tools.capabilities import list_capabilities


def answer_other(question: str, harness: RunHarness | None = None) -> str:
    """Reply to a greeting, or ask for a clearer analysis question."""

    normalized = question.strip().strip("！!。.?？ ").lower()
    if any(word in normalized for word in ("能做什么", "支持什么", "有哪些能力", "能查什么")):
        return (harness.call("other", "list_capabilities", list_capabilities)
                if harness else list_capabilities())
    if normalized in {"你好", "您好", "嗨", "hi", "hello"}:
        return "你好！你可以问我百亿补贴的 GMV、订单数或访客数，也可以问指标口径和任务状态。"
    return "请说明指标、日期或想分析的变化；也可以问我能做什么。"
