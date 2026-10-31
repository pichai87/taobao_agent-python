"""Business checks for the single synthetic commerce path and its safety boundary."""

import json
import sqlite3
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

from taobao_agent.commerce_data import (
    CATALOG_PATH, CommerceDataError, query_metric, seed_commerce_db,
)
from taobao_agent.evaluation import load_dataset, score_case
from taobao_agent.metric_catalog import load_metric_catalog
from taobao_agent.metric_sql import MetricQuery, build_metric_query
from taobao_agent.workflow import build_workflow, run_workflow


class FakeModel:
    def __init__(self, intent: dict[str, object], mode: str = "overall_change") -> None:
        self.intent = intent
        self.mode = mode
        self.tool_names: list[str] = []
        self.intent_calls = 0
        self.analysis_calls = 0

    def bind_tools(self, tools: list[object], *, tool_choice: str = "auto") -> "FakeModel":
        self.tool_names = [item.name for item in tools]
        return self

    def invoke(self, messages: list[object]) -> AIMessage:
        system = messages[0]
        assert isinstance(system, SystemMessage)
        if "意图识别入口" in system.content:
            self.intent_calls += 1
            return AIMessage(content=json.dumps(self.intent, ensure_ascii=False))
        if "分析专家" in system.content:
            self.analysis_calls += 1
            return AIMessage(content=json.dumps({"mode": self.mode}))
        if "取数专家" in system.content:
            request = json.loads(messages[1].content.split("已批准请求：", 1)[1])
            index = sum(isinstance(message, ToolMessage) for message in messages)
            name = self.tool_names[index]
            args = {"metric_names": request["metric_names"]}
            if name in {"build_sql", "execute_query"}:
                args.update(periods=request["periods"], group_by=request["group_by"])
            return AIMessage(content="", tool_calls=[{
                "name": name, "args": args, "id": f"tool-{index}",
            }])
        raise AssertionError("Unexpected model call")


def intent_payload(
    intent_type: str, metric: str = "GMV", *, dates: list[str] | None = None,
    metric_names: list[str] | None = None, date_range: dict[str, str] | None = None,
    dimensions: list[str] | None = None, secondary: list[str] | None = None,
) -> dict[str, object]:
    dates = dates or []
    return {
        "intent_type": intent_type, "secondary_intents": secondary or [], "confidence": 0.95,
        "metric_name": metric, "metric_field": None, "time_label": None,
        "prev": dates[0] if len(dates) > 1 else (date_range or {}).get("start"),
        "curr": dates[-1] if dates else (date_range or {}).get("end"),
        "dates": dates, "metric_names": metric_names or [], "date_range": date_range,
        "scope": "百亿补贴", "dimensions": dimensions or [],
    }


class CommerceWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.database = Path(temporary.name) / "commerce.sqlite"
        self.summary = seed_commerce_db(self.database)
        self.catalog = load_metric_catalog(CATALOG_PATH)

    def run_case(self, question: str, model: FakeModel):
        return run_workflow(
            question, model, date(2026, 11, 1), self.catalog, self.database,
            query_model=model, analysis_model=model,
        )

    def test_seed_is_reproducible_and_never_overwrites(self) -> None:
        self.assertEqual(self.summary.rows, 10)
        self.assertEqual((self.summary.first_date, self.summary.last_date),
                         ("2026-10-20", "2026-10-22"))
        with self.assertRaises(FileExistsError):
            seed_commerce_db(self.database)
        with closing(sqlite3.connect(self.database)) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(commerce_daily)")}
            self.assertTrue({"visitor_uv", "order_count", "pay_amount_cents", "industry"} <= columns)

    def test_one_query_path_handles_single_day_and_range_group_multi_metric(self) -> None:
        single = FakeModel(intent_payload("data_query", dates=["2026-10-22"]))
        result = self.run_case("2026-10-22 百亿补贴 GMV 多少？", single)
        self.assertIn("20600.00 元", result.answer)
        self.assertEqual(single.tool_names,
                         ["search_metrics", "rule_search", "build_sql", "execute_query"])
        self.assertEqual(single.intent_calls, 1)
        multi = FakeModel(intent_payload(
            "data_query", metric_names=["GMV", "访客数", "订单数"],
            date_range={"start": "2026-10-21", "end": "2026-10-22"},
            dimensions=["行业"],
        ))
        grouped = self.run_case("2026-10-21 至 2026-10-22 按行业看 GMV、访客数和订单数", multi)
        for expected in ("44600.00 元", "4900 人", "446 单", "家电", "服装", "食品"):
            self.assertIn(expected, grouped.answer)
        self.assertEqual(multi.tool_names, single.tool_names)

    def test_analysis_consumes_query_evidence_and_reconciles_funnel(self) -> None:
        model = FakeModel(intent_payload(
            "attribution", dates=["2026-10-21", "2026-10-22"], dimensions=["行业"]
        ))
        updates = list(build_workflow(
            model, date(2026, 11, 1), self.catalog, self.database,
            query_model=model, analysis_model=model,
        ).stream({"question": "2026-10-22 GMV 比 2026-10-21 为什么下降？按行业拆解"}))
        self.assertEqual([next(iter(item)) for item in updates],
                         ["intent", "supervisor", "query", "analysis"])
        evidence = updates[2]["query"]["query_evidence"]
        self.assertEqual(len(evidence["results"]), 6)
        answer = updates[3]["analysis"]["answer"]
        for expected in ("下降 3400.00 元", "访客数 2400→2500 人", "转化率 10.00%→8.24%",
                         "家电减少 3400.00 元", "不能单凭"):
            self.assertIn(expected, answer)
        self.assertEqual(model.analysis_calls, 1)

    def test_metadata_and_amount_share_the_same_query_path(self) -> None:
        model = FakeModel(intent_payload(
            "table_meta", dates=["2026-10-22"], secondary=["data_query"]
        ))
        result = self.run_case("GMV 如何计算？2026-10-22 是多少？", model)
        self.assertIn("指标口径：", result.answer)
        self.assertIn("commerce_daily.pay_amount_cents", result.answer)
        self.assertIn("查询结果：", result.answer)
        self.assertIn("20600.00 元", result.answer)

    def test_three_day_high_low_uses_the_same_query_evidence(self) -> None:
        model = FakeModel(intent_payload(
            "attribution", dates=["2026-10-20", "2026-10-21", "2026-10-22"]
        ), mode="high_low")
        result = self.run_case("2026-10-20、21、22 GMV 哪天最高最低？", model)
        self.assertIn("最高为 2026-10-20 的 24000.00 元", result.answer)
        self.assertIn("最低为 2026-10-22 的 20600.00 元", result.answer)

    def test_wrong_tool_parameters_are_rejected_before_query(self) -> None:
        class WrongDateModel(FakeModel):
            def invoke(self, messages: list[object]) -> AIMessage:
                response = super().invoke(messages)
                if response.tool_calls and response.tool_calls[0]["name"] == "build_sql":
                    response.tool_calls[0]["args"]["periods"][0]["start"] = "2026-10-21"
                return response

        model = WrongDateModel(intent_payload("data_query", dates=["2026-10-22"]))
        result = self.run_case("2026-10-22 GMV 多少？", model)
        self.assertIn("工具调用未通过顺序或参数校验", result.answer)
        self.assertNotIn("20600.00", result.answer)

    def test_malformed_tool_call_is_retried_with_approved_arguments(self) -> None:
        class MalformedOnceModel(FakeModel):
            def __init__(self, intent: dict[str, object]) -> None:
                super().__init__(intent)
                self.malformed_sent = False

            def invoke(self, messages: list[object]) -> AIMessage:
                if ("取数专家" in messages[0].content and not self.malformed_sent):
                    self.malformed_sent = True
                    return AIMessage(content="", invalid_tool_calls=[{
                        "name": "search_metrics", "args": "{", "id": "broken", "error": "parse",
                    }])
                return super().invoke(messages)

        model = MalformedOnceModel(intent_payload("data_query", dates=["2026-10-22"]))
        result = self.run_case("2026-10-22 GMV 多少？", model)
        self.assertTrue(model.malformed_sent)
        self.assertIn("20600.00 元", result.answer)

    def test_malformed_tool_call_stops_after_three_attempts(self) -> None:
        class AlwaysMalformedModel(FakeModel):
            def __init__(self, intent: dict[str, object]) -> None:
                super().__init__(intent)
                self.query_calls = 0

            def invoke(self, messages: list[object]) -> AIMessage:
                if "取数专家" in messages[0].content:
                    self.query_calls += 1
                    return AIMessage(content="", invalid_tool_calls=[{
                        "name": "search_metrics", "args": "{", "id": "broken", "error": "parse",
                    }])
                return super().invoke(messages)

        model = AlwaysMalformedModel(intent_payload("data_query", dates=["2026-10-22"]))
        result = self.run_case("2026-10-22 GMV 多少？", model)
        self.assertEqual(model.query_calls, 3)
        self.assertIn("连续三次", result.answer)
        self.assertNotIn("20600.00", result.answer)

    def test_unapproved_metric_and_tampered_sql_do_not_return_values(self) -> None:
        unknown = FakeModel(intent_payload("data_query", metric="退款率", dates=["2026-10-22"]))
        answer = self.run_case("2026-10-22 退款率是多少？", unknown).answer
        self.assertIn("指标不在已批准目录", answer)
        self.assertNotIn("退款率为", answer)
        metric = self.catalog[0]
        day = date(2026, 10, 22)
        approved = build_metric_query(metric, day, day, "行业")
        for candidate in (
            MetricQuery(approved.statement.replace("SUM", "MAX"), approved.parameters),
            MetricQuery(approved.statement, ("2026-10-21", "2026-10-22", "百亿补贴")),
        ):
            with self.subTest(candidate=candidate), self.assertRaises(CommerceDataError):
                query_metric(metric, day, day, self.database, "行业", candidate=candidate)

    def test_source_marker_and_range_limits_are_checked_at_database_boundary(self) -> None:
        metric = self.catalog[0]
        with self.assertRaises(CommerceDataError):
            query_metric(metric, date(2026, 10, 22), date(2026, 11, 30), self.database)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("UPDATE import_metadata SET source_id='unknown'")
            connection.commit()
        with self.assertRaises(CommerceDataError):
            query_metric(metric, date(2026, 10, 22), date(2026, 10, 22), self.database)

    def test_all_business_cases_pass_with_fixed_model_outputs(self) -> None:
        setup = {
            "single_day_gmv": (intent_payload("data_query", dates=["2026-10-22"]), "overall_change"),
            "range_multi_metric_industry": (intent_payload(
                "data_query", metric_names=["GMV", "访客数", "订单数"],
                date_range={"start": "2026-10-21", "end": "2026-10-22"}, dimensions=["行业"]
            ), "overall_change"),
            "gmv_decline_industry": (intent_payload(
                "attribution", dates=["2026-10-21", "2026-10-22"], dimensions=["行业"]
            ), "overall_change"),
            "three_day_high_low": (intent_payload(
                "attribution", dates=["2026-10-20", "2026-10-21", "2026-10-22"]
            ), "high_low"),
            "metric_metadata": (intent_payload("table_meta"), "overall_change"),
            "metadata_and_amount": (intent_payload(
                "table_meta", dates=["2026-10-22"], secondary=["data_query"]
            ), "overall_change"),
            "unknown_metric": (intent_payload(
                "data_query", metric="退款率", dates=["2026-10-22"]
            ), "overall_change"),
        }
        for case in load_dataset().cases:
            with self.subTest(case=case.id):
                payload, mode = setup[case.id]
                result = self.run_case(case.question, FakeModel(payload, mode))
                outcome = score_case(case, result)
                self.assertTrue(outcome.passed, outcome.checks)
