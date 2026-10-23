"""Stage 2 historical test snapshot; retained for study, not in the current test suite."""

import os
import io
import unittest
from unittest.mock import patch

from langchain_core.exceptions import ModelRateLimitError
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import ValidationError

from taobao_agent.minimal_agent import create_qwen_model, main, run_once


class FakeModel:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[list[object]] = []

    def invoke(self, messages: list[object]) -> AIMessage:
        self.calls.append(messages)
        return AIMessage(content=self.content)


class MinimalAgentTest(unittest.TestCase):
    def test_one_question_makes_one_model_call_and_returns_reply(self) -> None:
        model = FakeModel('{"answer":"当前没有业务数据，无法查询昨天的 GMV。"}')

        reply = run_once("昨天 GMV 多少？", model)

        self.assertEqual(reply.answer, "当前没有业务数据，无法查询昨天的 GMV。")
        self.assertEqual(len(model.calls), 1)
        self.assertIsInstance(model.calls[0][0], SystemMessage)
        self.assertIn("JSON", model.calls[0][0].content)
        self.assertIsInstance(model.calls[0][1], HumanMessage)
        self.assertEqual(model.calls[0][1].content, "昨天 GMV 多少？")

    def test_invalid_json_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            run_once("GMV 是什么？", FakeModel("这不是 JSON"))

    def test_unexpected_output_field_is_rejected(self) -> None:
        model = FakeModel('{"answer":"无法查询","gmv":123}')
        with self.assertRaises(ValidationError):
            run_once("昨天 GMV 多少？", model)

    def test_empty_question_does_not_call_model(self) -> None:
        model = FakeModel('{"answer":"不会使用"}')
        with self.assertRaisesRegex(ValueError, "Question must not be empty"):
            run_once("   ", model)
        self.assertEqual(model.calls, [])

    def test_missing_key_fails_before_creating_model(self) -> None:
        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": ""}):
            with self.assertRaisesRegex(RuntimeError, "DASHSCOPE_API_KEY"):
                create_qwen_model()

    def test_cli_explains_rate_limit_without_traceback(self) -> None:
        stderr = io.StringIO()
        with (
            patch("sys.argv", ["taobao-agent", "什么是 GMV？"]),
            patch("sys.stderr", stderr),
            patch("taobao_agent.minimal_agent.create_qwen_model", return_value=object()),
            patch(
                "taobao_agent.minimal_agent.run_once",
                side_effect=ModelRateLimitError("429 request limit"),
            ),
        ):
            with self.assertRaises(SystemExit) as result:
                main()

        self.assertEqual(result.exception.code, 3)
        self.assertIn("429", stderr.getvalue())
        self.assertIn("限流", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())
