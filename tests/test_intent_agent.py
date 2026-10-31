"""Test the intent contract without spending model requests."""

import json
import io
import unittest
from datetime import date
from unittest.mock import patch

from langchain_core.exceptions import ModelRateLimitError
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import ValidationError

from taobao_agent.intent_agent import main, run_intent_once


class FakeModel:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.calls: list[list[object]] = []

    def invoke(self, messages: list[object]) -> AIMessage:
        self.calls.append(messages)
        return AIMessage(content=json.dumps(self.payload, ensure_ascii=False))


class RateLimitedModel:
    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, messages: list[object]) -> AIMessage:
        self.calls += 1
        raise ModelRateLimitError("429 limit_requests")


def valid_payload() -> dict[str, object]:
    return {
        "intent_type": "attribution",
        "secondary_intents": [],
        "confidence": 0.9,
        "metric_name": "GMV",
        "metric_field": None,
        "time_label": "昨天比前天",
        "curr": "2026-06-14",
        "prev": "2026-06-13",
        "dates": [],
        "scope": None,
        "dimensions": ["行业"],
    }


class IntentAgentTest(unittest.TestCase):
    def test_extracts_intent_dates_and_dimension_in_one_call(self) -> None:
        payload = valid_payload()
        payload["source"] = "rule"
        model = FakeModel(payload)

        result = run_intent_once("按行业看昨天 GMV 比前天为什么下降？", model, date(2026, 6, 15))

        self.assertEqual(result.intent_type, "attribution")
        self.assertEqual(result.curr, date(2026, 6, 14))
        self.assertEqual(result.prev, date(2026, 6, 13))
        self.assertEqual(result.dimensions, ["行业"])
        self.assertEqual(result.source, "llm")
        self.assertEqual(len(model.calls), 1)
        self.assertIsInstance(model.calls[0][0], SystemMessage)
        self.assertIn("2026-06-15", model.calls[0][0].content)
        self.assertIsInstance(model.calls[0][1], HumanMessage)
        self.assertEqual(model.calls[0][1].content, "按行业看昨天 GMV 比前天为什么下降？")

    def test_unverified_metric_field_is_rejected(self) -> None:
        payload = valid_payload()
        payload["metric_field"] = "gmv_amt"

        with self.assertRaisesRegex(ValidationError, "verified semantic model"):
            run_intent_once("昨天 GMV 多少？", FakeModel(payload), date(2026, 6, 15))

    def test_keeps_secondary_intent_for_a_two_part_request(self) -> None:
        payload = valid_payload()
        payload["intent_type"] = "table_meta"
        payload["secondary_intents"] = ["data_query"]

        result = run_intent_once(
            "先看表结构，再查询昨天 GMV", FakeModel(payload), date(2026, 6, 15)
        )

        self.assertEqual(result.intent_type, "table_meta")
        self.assertEqual(result.secondary_intents, ["data_query"])

    def test_invalid_confidence_and_duplicate_secondary_intent_are_rejected(self) -> None:
        payload = valid_payload()
        payload["confidence"] = 1.2
        with self.assertRaises(ValidationError):
            run_intent_once("昨天 GMV 多少？", FakeModel(payload), date(2026, 6, 15))

        payload = valid_payload()
        payload["secondary_intents"] = ["data_query", "data_query"]
        with self.assertRaisesRegex(ValidationError, "duplicates"):
            run_intent_once("昨天 GMV 多少？", FakeModel(payload), date(2026, 6, 15))

    def test_missing_required_field_is_rejected(self) -> None:
        payload = valid_payload()
        del payload["curr"]

        with self.assertRaises(ValidationError):
            run_intent_once("昨天 GMV 多少？", FakeModel(payload), date(2026, 6, 15))

    def test_non_iso_date_is_rejected(self) -> None:
        payload = valid_payload()
        payload["curr"] = "昨天"

        with self.assertRaises(ValidationError):
            run_intent_once("昨天 GMV 多少？", FakeModel(payload), date(2026, 6, 15))

    def test_three_dates_keep_the_original_curr_prev_contract(self) -> None:
        payload = valid_payload()
        payload["prev"] = "2010-12-01"
        payload["curr"] = "2010-12-03"
        payload["dates"] = ["2010-12-01", "2010-12-02", "2010-12-03"]
        result = run_intent_once("比较三天交易明细金额", FakeModel(payload), date(2026, 6, 15))
        self.assertEqual([day.isoformat() for day in result.requested_dates()], payload["dates"])

        payload["dates"] = ["2010-12-01", "2010-12-01", "2010-12-03"]
        with self.assertRaisesRegex(ValidationError, "duplicates"):
            run_intent_once("比较三天交易明细金额", FakeModel(payload), date(2026, 6, 15))

    def test_range_accepts_matching_dates_but_rejects_conflicting_dates(self) -> None:
        payload = valid_payload()
        payload["prev"] = "2010-12-01"
        payload["curr"] = "2010-12-03"
        payload["date_range"] = {"start": "2010-12-01", "end": "2010-12-03"}
        payload["dates"] = ["2010-12-01", "2010-12-02", "2010-12-03"]

        result = run_intent_once("查询 2010-12-01 至 03", FakeModel(payload), date(2026, 6, 15))
        self.assertEqual(result.date_range.start, date(2010, 12, 1))
        self.assertEqual(result.date_range.end, date(2010, 12, 3))

        payload["dates"] = ["2010-12-01", "2010-12-04"]
        with self.assertRaisesRegex(ValidationError, "agree with date_range"):
            run_intent_once("查询 2010-12-01 至 03", FakeModel(payload), date(2026, 6, 15))

        payload["dates"] = ["2010-12-03", "2010-12-01"]
        with self.assertRaisesRegex(ValidationError, "agree with date_range"):
            run_intent_once("查询 2010-12-01 至 03", FakeModel(payload), date(2026, 6, 15))

    def test_rate_limit_uses_rule_for_observed_attribution_question(self) -> None:
        model = RateLimitedModel()

        result = run_intent_once("昨天 GMV 比前天为什么下降？", model, date(2026, 10, 23))

        self.assertEqual(model.calls, 1)
        self.assertEqual(result.intent_type, "attribution")
        self.assertEqual(result.curr, date(2026, 10, 22))
        self.assertEqual(result.prev, date(2026, 10, 21))
        self.assertEqual(result.metric_field, None)
        self.assertEqual(result.source, "rule")
        self.assertEqual(result.confidence, 0.0)

    def test_rate_limit_rule_handles_simple_query_and_dimension(self) -> None:
        for question, dimensions in (
            ("昨天 GMV 多少？", []),
            ("按行业看昨天的 GMV", ["行业"]),
        ):
            with self.subTest(question=question):
                result = run_intent_once(question, RateLimitedModel(), date(2026, 10, 23))
                self.assertEqual(result.intent_type, "data_query")
                self.assertEqual(result.dimensions, dimensions)
                self.assertEqual(result.source, "rule")

    def test_rate_limit_does_not_guess_an_unsupported_question(self) -> None:
        model = RateLimitedModel()

        with self.assertRaises(ModelRateLimitError):
            run_intent_once("上周 GMV 为什么下降？", model, date(2026, 10, 23))
        self.assertEqual(model.calls, 1)

    def test_cli_prints_rule_source_after_rate_limit(self) -> None:
        output = io.StringIO()
        model = RateLimitedModel()
        with (
            patch("sys.argv", ["taobao-intent", "昨天 GMV 多少？"]),
            patch("sys.stdout", output),
            patch("taobao_agent.intent_agent.create_qwen_model", return_value=model),
        ):
            main()

        result = json.loads(output.getvalue())
        self.assertEqual(result["intent_type"], "data_query")
        self.assertEqual(result["source"], "rule")
        self.assertEqual(model.calls, 1)
