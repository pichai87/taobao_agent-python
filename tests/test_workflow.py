"""Check the active graph and its local expert branches without external requests."""

import io
import json
import os
import sqlite3
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Sequence
from unittest.mock import patch

from langchain_core.exceptions import ModelRateLimitError
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from taobao_agent.demo_query_tool import DEMO_CATALOG_PATH, DemoQueryError
from taobao_agent.intent_agent import IntentResult
from taobao_agent.metric_catalog import load_metric_catalog
from taobao_agent.metric_search import MetricDefinition
from taobao_agent.model import create_qwen_model
from taobao_agent.query_agent import answer_query
from taobao_agent.uci_online_retail import UCI_CATALOG_PATH, UCIDateOutOfRange, UCIQueryError
from taobao_agent.workflow import build_workflow, main, run_workflow


class FakeModel:
    def __init__(
        self, intent_type: str, secondary_intents: list[str] | None = None,
        metric_name: str = "GMV", curr: str = "2026-10-22", prev: str | None = None,
    ) -> None:
        self.intent_type = intent_type
        self.secondary_intents = secondary_intents or []
        self.metric_name = metric_name
        self.curr = curr
        self.prev = prev
        self.calls: list[list[object]] = []

    def invoke(self, messages: list[object]) -> AIMessage:
        self.calls.append(messages)
        return AIMessage(
            content=json.dumps(
                {
                    "intent_type": self.intent_type,
                    "secondary_intents": self.secondary_intents,
                    "confidence": 0.9,
                    "metric_name": self.metric_name if self.intent_type != "other" else None,
                    "metric_field": None,
                    "time_label": "昨天" if self.intent_type != "other" else None,
                    "curr": self.curr if self.intent_type != "other" else None,
                    "prev": self.prev,
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
    def setUp(self) -> None:
        demo_setting = patch.dict(os.environ, {"TAOBAO_DEMO_DATA": "0"})
        demo_setting.start()
        self.addCleanup(demo_setting.stop)

    def test_one_question_flows_from_intent_to_supervisor(self) -> None:
        model = FakeModel("attribution")

        result = run_workflow("昨天 GMV 比前天为什么下降？", model, date(2026, 10, 23))

        self.assertEqual(result.intent.intent_type, "attribution")
        self.assertEqual(result.next_expert, "analysis")
        self.assertIn("两个不同的明确日期", result.answer)
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

    def test_demo_query_runs_through_the_existing_graph(self) -> None:
        model = FakeModel("data_query")
        catalog = load_metric_catalog(DEMO_CATALOG_PATH)

        result = run_workflow(
            "2026-10-22 GMV 多少？", model, date(2026, 10, 23), catalog, demo_data=True
        )

        self.assertEqual(result.next_expert, "query")
        self.assertIn("教学合成数据", result.answer)
        self.assertIn("2026-10-22", result.answer)
        self.assertIn("200.00 元", result.answer)
        self.assertEqual(len(model.calls), 1)

    def test_uci_query_runs_through_existing_graph(self) -> None:
        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "retail.sqlite"
            with closing(sqlite3.connect(database_path)) as connection:
                connection.executescript(
                    "CREATE TABLE retail_lines (invoice_date TEXT, record_type TEXT, line_amount_pence INTEGER);"
                    "CREATE TABLE import_metadata (source_id TEXT, source_sha256 TEXT, first_date TEXT, last_date TEXT);"
                    "INSERT INTO import_metadata VALUES ('uci-online-retail-352', 'fixture', '2010-12-01', '2010-12-02');"
                    "INSERT INTO retail_lines VALUES ('2010-12-01', 'positive', 635);"
                    "INSERT INTO retail_lines VALUES ('2010-12-01', 'cancel', -255);"
                )
            model = FakeModel("data_query", metric_name="交易明细金额", curr="2010-12-01")
            result = run_workflow(
                "2010-12-01 交易明细金额是多少？", model, date(2026, 10, 25),
                load_metric_catalog(UCI_CATALOG_PATH), uci_db_path=database_path,
            )

        self.assertEqual(result.next_expert, "query")
        self.assertIn("6.35 英镑", result.answer)
        self.assertIn("不是已支付 GMV", result.answer)
        self.assertEqual(len(model.calls), 1)

    def test_uci_two_day_comparison_runs_through_analysis_node(self) -> None:
        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "retail.sqlite"
            with closing(sqlite3.connect(database_path)) as connection:
                connection.executescript(
                    "CREATE TABLE retail_lines (invoice_date TEXT, record_type TEXT, line_amount_pence INTEGER);"
                    "CREATE TABLE import_metadata (source_id TEXT, source_sha256 TEXT, first_date TEXT, last_date TEXT);"
                    "INSERT INTO import_metadata VALUES ('uci-online-retail-352', 'fixture', '2010-12-01', '2010-12-02');"
                    "INSERT INTO retail_lines VALUES ('2010-12-01', 'positive', 10000);"
                    "INSERT INTO retail_lines VALUES ('2010-12-02', 'positive', 8000);"
                    "INSERT INTO retail_lines VALUES ('2010-12-02', 'cancel', -5000);"
                )
            model = FakeModel(
                "attribution", metric_name="交易明细金额",
                curr="2010-12-02", prev="2010-12-01",
            )
            result = run_workflow(
                "2010-12-02 交易明细金额为什么比 2010-12-01 下降？",
                model, date(2026, 10, 25), load_metric_catalog(UCI_CATALOG_PATH),
                uci_db_path=database_path,
            )

        self.assertEqual(result.next_expert, "analysis")
        self.assertIn("2010-12-01 的交易明细金额为 100.00 英镑", result.answer)
        self.assertIn("2010-12-02 为 80.00 英镑", result.answer)
        self.assertIn("下降 20.00 英镑，变化率 20.00%", result.answer)
        self.assertIn("不能判断变化原因", result.answer)
        self.assertEqual(len(model.calls), 1)

    def test_attribution_passes_structured_query_evidence_to_analysis(self) -> None:
        model = FakeModel("attribution", prev="2026-10-21")
        updates = list(
            build_workflow(
                model, date(2026, 10, 23), load_metric_catalog(DEMO_CATALOG_PATH),
                demo_data=True,
            ).stream({"question": "2026-10-22 GMV 比 2026-10-21 下降多少？"})
        )

        self.assertEqual([next(iter(update)) for update in updates], [
            "intent", "supervisor", "query", "analysis",
        ])
        evidence = updates[2]["query"]["query_evidence"]
        self.assertEqual(evidence["previous_minor"], 30000)
        self.assertEqual(evidence["current_minor"], 20000)
        self.assertNotIn("answer", updates[2]["query"])
        self.assertIn("下降 100.00 元", updates[3]["analysis"]["answer"])
        self.assertEqual(len(model.calls), 1)

    def test_failed_comparison_stops_after_query_without_partial_evidence(self) -> None:
        model = FakeModel(
            "attribution", metric_name="交易明细金额",
            prev="2010-12-01", curr="2010-12-02",
        )
        with patch(
            "taobao_agent.daily_metric_tool.query_uci_metric",
            side_effect=[123456, UCIQueryError("private path")],
        ):
            updates = list(
                build_workflow(
                    model, date(2026, 10, 25), load_metric_catalog(UCI_CATALOG_PATH),
                    uci_db_path=Path("unused.sqlite"),
                ).stream({"question": "2010-12-02 交易明细金额为什么下降？"})
            )

        self.assertEqual([next(iter(update)) for update in updates], [
            "intent", "supervisor", "query",
        ])
        self.assertIsNone(updates[2]["query"]["query_evidence"])
        self.assertIn("没有返回比较结果", updates[2]["query"]["answer"])
        self.assertNotIn("1234.56", updates[2]["query"]["answer"])
        self.assertNotIn("private path", updates[2]["query"]["answer"])

    def test_uci_query_rejects_comparison_and_explains_date_coverage(self) -> None:
        catalog = load_metric_catalog(UCI_CATALOG_PATH)
        intent = IntentResult(
            intent_type="data_query",
            secondary_intents=[],
            confidence=0.9,
            metric_name="交易明细金额",
            metric_field=None,
            time_label="2010-12-01",
            curr="2010-12-01",
            prev=None,
            scope=None,
            dimensions=[],
        )
        with patch("taobao_agent.daily_metric_tool.query_uci_metric") as query_tool:
            compared = intent.model_copy(update={"prev": date(2010, 12, 2)})
            answer = answer_query(compared, catalog, uci_db_path=Path("unused.sqlite"))
            self.assertIn("无分组", answer)
            query_tool.assert_not_called()

            query_tool.side_effect = UCIDateOutOfRange("2010-12-01", "2011-12-09")
            answer = answer_query(intent, catalog, uci_db_path=Path("unused.sqlite"))
            self.assertIn("2010-12-01 至 2011-12-09", answer)
            self.assertIn("没有返回数值", answer)

    def test_main_cli_uci_mode_loads_public_metric_and_queries(self) -> None:
        model = FakeModel("data_query", metric_name="交易明细金额", curr="2010-12-01")
        output = io.StringIO()
        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "retail.sqlite"
            with closing(sqlite3.connect(database_path)) as connection:
                connection.executescript(
                    "CREATE TABLE retail_lines (invoice_date TEXT, record_type TEXT, line_amount_pence INTEGER);"
                    "CREATE TABLE import_metadata (source_id TEXT, source_sha256 TEXT, first_date TEXT, last_date TEXT);"
                    "INSERT INTO import_metadata VALUES ('uci-online-retail-352', 'fixture', '2010-12-01', '2010-12-01');"
                    "INSERT INTO retail_lines VALUES ('2010-12-01', 'positive', 635);"
                )
            with (
                patch.dict(
                    os.environ,
                    {
                        "TAOBAO_UCI_DB_PATH": str(database_path),
                        "TAOBAO_DEMO_DATA": "0",
                        "TAOBAO_METRIC_CATALOG_PATH": "",
                    },
                ),
                patch("sys.argv", ["taobao-agent", "2010-12-01 交易明细金额是多少？"]),
                patch("sys.stdout", output),
                patch("taobao_agent.workflow.create_qwen_model", return_value=model),
            ):
                main()

        result = json.loads(output.getvalue())
        self.assertEqual(result["next_expert"], "query")
        self.assertIn("6.35 英镑", result["answer"])
        self.assertEqual(len(model.calls), 1)

    def test_main_cli_rejects_missing_uci_database_before_model(self) -> None:
        stderr = io.StringIO()
        with TemporaryDirectory() as directory:
            with (
                patch.dict(
                    os.environ,
                    {
                        "TAOBAO_UCI_DB_PATH": str(Path(directory) / "missing.sqlite"),
                        "TAOBAO_DEMO_DATA": "0",
                        "TAOBAO_METRIC_CATALOG_PATH": "",
                    },
                ),
                patch("sys.argv", ["taobao-agent", "2010-12-01 交易明细金额是多少？"]),
                patch("sys.stderr", stderr),
                patch("taobao_agent.workflow.create_qwen_model") as create_model,
            ):
                with self.assertRaises(SystemExit) as result:
                    main()

        self.assertEqual(result.exception.code, 2)
        self.assertIn("数据库文件不存在", stderr.getvalue())
        create_model.assert_not_called()

    def test_demo_data_does_not_run_without_explicit_opt_in(self) -> None:
        result = run_workflow(
            "2026-10-22 GMV 多少？",
            FakeModel("data_query"),
            date(2026, 10, 23),
            load_metric_catalog(DEMO_CATALOG_PATH),
        )

        self.assertIn("当前尚未接入数据查询", result.answer)
        self.assertNotIn("200.00 元", result.answer)

    def test_demo_query_requires_a_supported_single_day_question(self) -> None:
        catalog = load_metric_catalog(DEMO_CATALOG_PATH)
        intent = IntentResult(
            intent_type="data_query",
            secondary_intents=[],
            confidence=0.9,
            metric_name="GMV",
            metric_field=None,
            time_label=None,
            curr=None,
            prev=None,
            scope=None,
            dimensions=[],
        )
        with patch("taobao_agent.daily_metric_tool.query_demo_metric") as query_tool:
            self.assertIn("明确的查询日期", answer_query(intent, catalog, demo_data=True))
            grouped = intent.model_copy(
                update={"curr": date(2026, 10, 22), "dimensions": ["行业"]}
            )
            self.assertIn("无分组", answer_query(grouped, catalog, demo_data=True))
            compared = intent.model_copy(
                update={"curr": date(2026, 10, 22), "prev": date(2026, 10, 21)}
            )
            self.assertIn("单日", answer_query(compared, catalog, demo_data=True))
            query_tool.assert_not_called()

    def test_demo_query_hides_internal_tool_failure(self) -> None:
        with patch(
            "taobao_agent.daily_metric_tool.query_demo_metric",
            side_effect=DemoQueryError("private demo file path"),
        ):
            result = run_workflow(
                "2026-10-22 GMV 多少？",
                FakeModel("data_query"),
                date(2026, 10, 23),
                load_metric_catalog(DEMO_CATALOG_PATH),
                demo_data=True,
            )

        self.assertIn("教学数据查询未通过校验", result.answer)
        self.assertNotIn("private demo file path", result.answer)

    def test_query_rejects_tool_value_for_a_different_date(self) -> None:
        with patch("taobao_agent.query_agent.make_daily_metric_tool") as make_tool:
            make_tool.return_value.invoke.return_value = {
                "metric_name": "GMV", "business_date": "2026-10-21",
                "amount_minor": 20000, "source": "demo", "unit": "fen",
            }
            result = run_workflow(
                "2026-10-22 GMV 多少？", FakeModel("data_query"), date(2026, 10, 23),
                load_metric_catalog(DEMO_CATALOG_PATH), demo_data=True,
            )

        self.assertIn("查询未通过校验", result.answer)
        self.assertNotIn("200.00 元", result.answer)

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
        self.assertIn("尚未配置 GMV", result.answer)
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
        self.assertIn("两个不同的明确日期", result["answer"])
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

    def test_main_cli_demo_mode_uses_the_built_in_catalog(self) -> None:
        model = FakeModel("data_query")
        output = io.StringIO()
        with (
            patch.dict(
                os.environ,
                {"TAOBAO_DEMO_DATA": "1", "TAOBAO_METRIC_CATALOG_PATH": ""},
            ),
            patch("sys.argv", ["taobao-agent", "2026-10-22 GMV 多少？"]),
            patch("sys.stdout", output),
            patch("taobao_agent.workflow.create_qwen_model", return_value=model),
        ):
            main()

        result = json.loads(output.getvalue())
        self.assertEqual(result["next_expert"], "query")
        self.assertIn("200.00 元", result["answer"])
        self.assertEqual(len(model.calls), 1)

    def test_main_cli_rejects_mixed_demo_and_custom_catalog(self) -> None:
        stderr = io.StringIO()
        with (
            patch.dict(
                os.environ,
                {"TAOBAO_DEMO_DATA": "1", "TAOBAO_METRIC_CATALOG_PATH": "custom.json"},
            ),
            patch("sys.argv", ["taobao-agent", "2026-10-22 GMV 多少？"]),
            patch("sys.stderr", stderr),
            patch("taobao_agent.workflow.create_qwen_model") as create_model,
        ):
            with self.assertRaises(SystemExit) as result:
                main()

        self.assertEqual(result.exception.code, 2)
        self.assertIn("不能同时设置", stderr.getvalue())
        create_model.assert_not_called()

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
