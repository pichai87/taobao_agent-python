"""Check the active graph and its local expert branches without external requests."""

import io
import json
import os
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Sequence
from unittest.mock import patch

from langchain_core.exceptions import ModelRateLimitError
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from taobao_agent.metric_search import MetricDefinition
from taobao_agent.model import create_qwen_model
from taobao_agent.workflow import main, run_workflow


class FakeModel:
    def __init__(self, intent_type: str, secondary_intents: list[str] | None = None) -> None:
        self.intent_type = intent_type
        self.secondary_intents = secondary_intents or []
        self.calls: list[list[object]] = []

    def invoke(self, messages: list[object]) -> AIMessage:
        self.calls.append(messages)
        return AIMessage(
            content=json.dumps(
                {
                    "intent_type": self.intent_type,
                    "secondary_intents": self.secondary_intents,
                    "confidence": 0.9,
                    "metric_name": "GMV" if self.intent_type != "other" else None,
                    "metric_field": None,
                    "time_label": "昨天" if self.intent_type != "other" else None,
                    "curr": "2026-10-22" if self.intent_type != "other" else None,
                    "prev": None,
                    "scope": None,
                    "dimensions": [],
                },
                ensure_ascii=False,
            )
        )


class RateLimitedModel:
    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, messages: list[object]) -> AIMessage:
        self.calls += 1
        raise ModelRateLimitError("429 limit_requests")


class TimeoutCatalog(Sequence[MetricDefinition]):
    def __len__(self) -> int:
        return 1

    def __getitem__(self, index: int) -> MetricDefinition:
        raise TimeoutError("private catalog endpoint timed out")


class WorkflowTest(unittest.TestCase):
    def test_one_question_flows_from_intent_to_supervisor(self) -> None:
        model = FakeModel("attribution")

        result = run_workflow("昨天 GMV 比前天为什么下降？", model, date(2026, 10, 23))

        self.assertEqual(result.intent.intent_type, "attribution")
        self.assertEqual(result.next_expert, "analysis")
        self.assertIsNone(result.answer)
        self.assertEqual(len(model.calls), 1)
        self.assertIsInstance(model.calls[0][0], SystemMessage)
        self.assertIsInstance(model.calls[0][1], HumanMessage)

    def test_routes_query_table_metadata_and_other(self) -> None:
        for intent_type, expert in (
            ("data_query", "query"),
            ("table_meta", "query"),
            ("other", "other"),
        ):
            with self.subTest(intent_type=intent_type):
                model = FakeModel(intent_type)
                result = run_workflow("测试问题", model, date(2026, 10, 23))
                self.assertEqual(result.next_expert, expert)
                if expert == "query":
                    self.assertIn("尚未配置 GMV", result.answer)
                else:
                    self.assertIn("请说明", result.answer)
                self.assertEqual(len(model.calls), 1)

    def test_query_uses_supplied_metric_definition(self) -> None:
        model = FakeModel("data_query")
        catalog = (
            MetricDefinition(
                name="GMV",
                table="demo_sales_daily",
                field="paid_amount",
                source="test fixture",
            ),
        )

        result = run_workflow("昨天 GMV 多少？", model, date(2026, 10, 23), catalog)

        self.assertEqual(result.next_expert, "query")
        self.assertIn("demo_sales_daily.paid_amount", result.answer)
        self.assertIn("test fixture", result.answer)
        self.assertIn("无法返回数值", result.answer)
        self.assertEqual(len(model.calls), 1)

    def test_query_does_not_guess_from_an_unrelated_definition(self) -> None:
        model = FakeModel("data_query")
        catalog = (
            MetricDefinition(
                name="订单量",
                table="demo_sales_daily",
                field="order_count",
                source="test fixture",
            ),
        )

        result = run_workflow("昨天 GMV 多少？", model, date(2026, 10, 23), catalog)

        self.assertIn("尚未配置 GMV", result.answer)
        self.assertNotIn("order_count", result.answer)
        self.assertEqual(len(model.calls), 1)

    def test_query_reports_tool_timeout_without_exposing_internal_details(self) -> None:
        model = FakeModel("data_query")

        result = run_workflow(
            "昨天 GMV 多少？", model, date(2026, 10, 23), TimeoutCatalog()
        )

        self.assertEqual(result.next_expert, "query")
        self.assertIn("指标目录暂时不可用", result.answer)
        self.assertNotIn("private catalog endpoint", result.answer)
        self.assertEqual(len(model.calls), 1)

    def test_query_reports_connection_failure(self) -> None:
        with patch(
            "taobao_agent.harness.search_metrics",
            side_effect=ConnectionError("private catalog address"),
        ):
            result = run_workflow(
                "昨天 GMV 多少？", FakeModel("data_query"), date(2026, 10, 23)
            )

        self.assertIn("指标目录暂时不可用", result.answer)
        self.assertNotIn("private catalog address", result.answer)

    def test_query_does_not_hide_programming_errors(self) -> None:
        with patch("taobao_agent.harness.search_metrics", side_effect=ValueError("bad code")):
            with self.assertRaisesRegex(ValueError, "bad code"):
                run_workflow("昨天 GMV 多少？", FakeModel("data_query"), date(2026, 10, 23))

    def test_other_greeting_runs_expert_without_second_model_call(self) -> None:
        model = FakeModel("other")

        result = run_workflow("你好！", model, date(2026, 10, 23))

        self.assertEqual(result.next_expert, "other")
        self.assertIn("你好", result.answer)
        self.assertEqual(len(model.calls), 1)

    def test_keeps_secondary_intent_for_later_planning(self) -> None:
        result = run_workflow(
            "先看表结构再查数",
            FakeModel("table_meta", ["data_query"]),
            date(2026, 10, 23),
        )

        self.assertEqual(result.intent.secondary_intents, ["data_query"])
        self.assertEqual(result.next_expert, "query")
        self.assertIn("尚未配置 GMV", result.answer)

    def test_rate_limit_rule_result_reaches_supervisor(self) -> None:
        model = RateLimitedModel()

        result = run_workflow("昨天 GMV 比前天为什么下降？", model, date(2026, 10, 23))

        self.assertEqual(result.intent.source, "rule")
        self.assertEqual(result.next_expert, "analysis")
        self.assertIsNone(result.answer)
        self.assertEqual(model.calls, 1)

    def test_empty_question_stops_before_model_call(self) -> None:
        model = FakeModel("other")

        with self.assertRaisesRegex(ValueError, "Question must not be empty"):
            run_workflow("  ", model, date(2026, 10, 23))
        self.assertEqual(model.calls, [])

    def test_main_cli_prints_the_handoff(self) -> None:
        model = FakeModel("attribution")
        output = io.StringIO()
        with (
            patch("sys.argv", ["taobao-agent", "昨天 GMV 为什么下降？"]),
            patch("sys.stdout", output),
            patch("taobao_agent.workflow.create_qwen_model", return_value=model),
        ):
            main()

        result = json.loads(output.getvalue())
        self.assertEqual(result["next_expert"], "analysis")
        self.assertEqual(result["intent"]["intent_type"], "attribution")
        self.assertIsNone(result["answer"])
        self.assertEqual(len(model.calls), 1)

    def test_main_cli_prints_other_answer(self) -> None:
        model = FakeModel("other")
        output = io.StringIO()
        with (
            patch("sys.argv", ["taobao-agent", "你好"]),
            patch("sys.stdout", output),
            patch("taobao_agent.workflow.create_qwen_model", return_value=model),
        ):
            main()

        result = json.loads(output.getvalue())
        self.assertEqual(result["next_expert"], "other")
        self.assertIn("你好", result["answer"])
        self.assertEqual(len(model.calls), 1)

    def test_main_cli_loads_metric_catalog_before_query(self) -> None:
        model = FakeModel("data_query")
        output = io.StringIO()
        with TemporaryDirectory() as directory:
            catalog_path = Path(directory) / "metrics.json"
            catalog_path.write_text(
                json.dumps(
                    [
                        {
                            "name": "GMV",
                            "table": "demo_sales_daily",
                            "field": "paid_amount",
                            "source": "test fixture",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            with (
                patch.dict(os.environ, {"TAOBAO_METRIC_CATALOG_PATH": str(catalog_path)}),
                patch("sys.argv", ["taobao-agent", "昨天 GMV 多少？"]),
                patch("sys.stdout", output),
                patch("taobao_agent.workflow.create_qwen_model", return_value=model),
            ):
                main()

        result = json.loads(output.getvalue())
        self.assertEqual(result["next_expert"], "query")
        self.assertIn("demo_sales_daily.paid_amount", result["answer"])
        self.assertEqual(len(model.calls), 1)

    def test_main_cli_keeps_empty_catalog_without_path(self) -> None:
        model = FakeModel("data_query")
        output = io.StringIO()
        with (
            patch.dict(os.environ, {"TAOBAO_METRIC_CATALOG_PATH": ""}),
            patch("sys.argv", ["taobao-agent", "昨天 GMV 多少？"]),
            patch("sys.stdout", output),
            patch("taobao_agent.workflow.create_qwen_model", return_value=model),
        ):
            main()

        result = json.loads(output.getvalue())
        self.assertEqual(result["next_expert"], "query")
        self.assertIn("尚未配置 GMV", result["answer"])
        self.assertEqual(len(model.calls), 1)

    def test_main_cli_rejects_invalid_catalog_before_model_call(self) -> None:
        stderr = io.StringIO()
        with TemporaryDirectory() as directory:
            catalog_path = Path(directory) / "metrics.json"
            catalog_path.write_text('[{"name":"GMV"}]', encoding="utf-8")
            with (
                patch.dict(os.environ, {"TAOBAO_METRIC_CATALOG_PATH": str(catalog_path)}),
                patch("sys.argv", ["taobao-agent", "昨天 GMV 多少？"]),
                patch("sys.stderr", stderr),
                patch("taobao_agent.workflow.create_qwen_model") as create_model,
            ):
                with self.assertRaises(SystemExit) as result:
                    main()

        self.assertEqual(result.exception.code, 2)
        self.assertIn("指标目录格式无效", stderr.getvalue())
        create_model.assert_not_called()

    def test_main_cli_rejects_incomplete_calculation_before_model_call(self) -> None:
        stderr = io.StringIO()
        with TemporaryDirectory() as directory:
            catalog_path = Path(directory) / "metrics.json"
            catalog_path.write_text(
                json.dumps(
                    [
                        {
                            "name": "GMV",
                            "table": "demo_orders",
                            "field": "order_amount_fen",
                            "source": "test fixture",
                            "calculation": {"aggregation": "sum"},
                        }
                    ]
                ),
                encoding="utf-8",
            )
            with (
                patch.dict(os.environ, {"TAOBAO_METRIC_CATALOG_PATH": str(catalog_path)}),
                patch("sys.argv", ["taobao-agent", "昨天 GMV 多少？"]),
                patch("sys.stderr", stderr),
                patch("taobao_agent.workflow.create_qwen_model") as create_model,
            ):
                with self.assertRaises(SystemExit) as result:
                    main()

        self.assertEqual(result.exception.code, 2)
        self.assertIn("calculation", stderr.getvalue())
        create_model.assert_not_called()

    def test_main_cli_explains_rate_limit_without_traceback(self) -> None:
        stderr = io.StringIO()
        with (
            patch("sys.argv", ["taobao-agent", "上周 GMV 为什么下降？"]),
            patch("sys.stderr", stderr),
            patch("taobao_agent.workflow.create_qwen_model", return_value=RateLimitedModel()),
        ):
            with self.assertRaises(SystemExit) as result:
                main()

        self.assertEqual(result.exception.code, 3)
        self.assertIn("429", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_missing_key_fails_before_creating_model(self) -> None:
        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": ""}):
            with self.assertRaisesRegex(RuntimeError, "DASHSCOPE_API_KEY"):
                create_qwen_model()
