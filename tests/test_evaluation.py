"""Check that the business scorecard detects observable errors."""

import unittest
from datetime import date

from taobao_agent.evaluation import load_dataset, score_case
from taobao_agent.intent_agent import IntentResult
from taobao_agent.workflow import WorkflowResult


class EvaluationTest(unittest.TestCase):
    def test_current_dataset_covers_the_single_commerce_path(self) -> None:
        dataset = load_dataset()
        self.assertEqual(len(dataset.cases), 7)
        self.assertEqual(len({case.id for case in dataset.cases}), 7)
        self.assertEqual({case.expected_expert for case in dataset.cases}, {"query", "analysis"})
        self.assertTrue(any(case.expected_range for case in dataset.cases))
        self.assertTrue(any(case.expected_dimensions == ("行业",) for case in dataset.cases))

    def test_score_detects_wrong_intent_and_unsupported_answer(self) -> None:
        case = next(case for case in load_dataset().cases if case.id == "unknown_metric")
        intent = IntentResult(
            intent_type="data_query", secondary_intents=[], confidence=0.9,
            metric_name="退款率", metric_field=None, time_label=None,
            curr="2026-10-22", prev=None, dates=["2026-10-22"],
            scope="百亿补贴", dimensions=[],
        )
        result = WorkflowResult(
            intent=intent, next_expert="query",
            answer="查询未完成：指标不在已批准目录中；没有返回数据。",
        )
        self.assertTrue(score_case(case, result).passed)
        invented = result.model_copy(update={"answer": "退款率为 5%"})
        checks = score_case(case, invented, diagnostic=True)
        self.assertFalse(checks.passed)
        self.assertFalse(checks.checks["required_answer"])
        self.assertFalse(checks.checks["forbidden_answer"])
        self.assertIn("退款率为", checks.observed["answer"])
