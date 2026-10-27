"""The single place that creates the configured chat model."""

import os
from collections.abc import Sequence
from typing import Protocol

from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI


class ChatModel(Protocol):
    def invoke(self, messages: list[BaseMessage]) -> AIMessage: ...


class ToolCallingModel(ChatModel, Protocol):
    def bind_tools(
        self, tools: Sequence[BaseTool], *, tool_choice: str = "auto"
    ) -> ChatModel: ...


def create_qwen_model() -> ToolCallingModel:
    """Read the current command's credentials without storing them in the project."""

    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("请先在运行项目的终端设置 DASHSCOPE_API_KEY 环境变量")

    model = ChatOpenAI(
        model=os.environ.get("DASHSCOPE_MODEL", "qwen-turbo"),
        api_key=api_key,
        base_url=os.environ.get(
            "DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
        ),
        temperature=0,
        max_retries=0,
        timeout=30,
    )
    return model.bind(response_format={"type": "json_object"})
