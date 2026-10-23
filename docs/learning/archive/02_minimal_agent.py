"""Stage 2 historical snapshot; not imported or run by the current project."""

import argparse
import json
from typing import NotRequired, TypedDict

from langchain_core.exceptions import ModelRateLimitError
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field

from taobao_agent.model import ChatModel, create_qwen_model


SYSTEM_PROMPT = (
    "你是电商数据分析学习项目的最小助手。当前没有连接业务数据或查询工具。"
    "请只输出一个 JSON 对象，且仅包含 answer 字段，其值为非空字符串。"
    "如果问题需要具体业务数据，请明确说明当前无法查询，不要编造数值。"
)


class MinimalReply(BaseModel):
    """The only structured output required in stage 2."""

    model_config = ConfigDict(extra="forbid")

    answer: str = Field(min_length=1)


class MinimalState(TypedDict):
    """Data passed through the one-node graph."""

    question: str
    reply: NotRequired[dict[str, str]]


def build_graph(model: ChatModel):
    """Compile START -> answer -> END with an injected chat model."""

    def answer_node(state: MinimalState) -> dict[str, dict[str, str]]:
        response = model.invoke(
            [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=state["question"])]
        )
        if not isinstance(response.content, str):
            raise ValueError("Model response must be a JSON string")
        reply = MinimalReply.model_validate_json(response.content)
        return {"reply": reply.model_dump()}

    graph = StateGraph(MinimalState)
    graph.add_node("answer", answer_node)
    graph.add_edge(START, "answer")
    graph.add_edge("answer", END)
    return graph.compile()


def run_once(question: str, model: ChatModel) -> MinimalReply:
    """Run one question through the graph and validate its reply."""

    if not question.strip():
        raise ValueError("Question must not be empty")
    state = build_graph(model).invoke({"question": question.strip()})
    return MinimalReply.model_validate(state["reply"])


def main() -> None:
    """CLI entry point for a single user question."""

    parser = argparse.ArgumentParser(description="最小电商数据分析 Agent")
    parser.add_argument("question", help="输入一个问题")
    args = parser.parse_args()

    try:
        model = create_qwen_model()
    except RuntimeError as exc:
        parser.exit(2, f"{exc}\n")

    try:
        reply = run_once(args.question, model)
    except ModelRateLimitError:
        parser.exit(
            3,
            "百炼返回 429：当前模型请求受到限流。请稍后只重试一次；"
            "若持续发生，请在百炼控制台检查该模型的限流与调用量。\n",
        )
    print(json.dumps(reply.model_dump(), ensure_ascii=False))
