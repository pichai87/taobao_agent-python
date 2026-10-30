"""Check the business evaluation contract without making model requests."""

import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from taobao_agent.evaluation import (
    GoldenDataset, evaluate_dataset, load_dataset, score_case,
)
from taobao_agent.intent_agent import IntentResult
from taobao_agent.workflow import WorkflowResult


def result(answer: str = "58960.79 英镑；不是已支付 GMV") -> WorkflowResult:
    return WorkflowResult(
        intent=IntentResult(
            intent_type="data_query", secondary_intents=[], confidence=0.9,
            metric_name="交易明细金额", metric_field=None,
            time_label="2010-12-01", curr="2010-12-01", prev=None,
            dates=[], scope=None, dimensions=[],
        ),
        next_expert="query", answer=answer,
    )


class EvaluationTest(unittest.TestCase):
    def test_golden_dataset_is_valid_and_has_business_scenarios(self) -> None:
        dataset = load_dataset()
        self.assertEqual(len(dataset.cases), 7)
        self.assertEqual(dataset.reference_date, date(2026, 10, 30))
        self.assertIn("metadata_and_amount", {case.id for case in dataset.cases})

    def test_score_separates_intent_route_dates_and_answer(self) -> None:
        case = load_dataset().cases[0]
        good = score_case(case, result())
        self.assertTrue(good.passed)
        bad = score_case(case, result("没有数据"))
        self.assertFalse(bad.passed)
        self.assertFalse(bad.checks["required_answer"])
        self.assertTrue(bad.checks["intents"])

    def test_runner_keeps_case_errors_visible_and_continues(self) -> None:
        case = load_dataset().cases[0]
        dataset = GoldenDataset(reference_date="2026-10-30", cases=(
            case, case.model_copy(update={"id": "second"}),
        ))
        with patch("taobao_agent.evaluation.run_workflow", side_effect=[result(), RuntimeError("secret")]) as workflow:
            report = evaluate_dataset(dataset, Path("unused.sqlite"), model=object())
        self.assertEqual(workflow.call_count, 2)
        self.assertEqual((report.passed, report.total), (1, 2))
        self.assertEqual(report.cases[1].error_type, "RuntimeError")
        self.assertNotIn("secret", report.model_dump_json())


if __name__ == "__main__":
    unittest.main()
