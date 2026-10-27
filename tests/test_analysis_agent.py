"""Business checks for dated observations and incomplete evidence."""

import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from taobao_agent.analysis_agent import MetricSeriesEvidence, answer_analysis
from taobao_agent.daily_metric_tool import DailyMetricValue
from taobao_agent.intent_agent import IntentResult
from taobao_agent.metric_catalog import load_metric_catalog
from taobao_agent.query_agent import fetch_metric_series
from taobao_agent.uci_online_retail import UCI_CATALOG_PATH, UCIDateOutOfRange, UCIQueryError


def intent(dates: list[str] | None = None) -> IntentResult:
    return IntentResult(
        intent_type="attribution", secondary_intents=[], confidence=0.9,
        metric_name="交易明细金额", metric_field=None, time_label="指定日期",
        curr=(dates or ["2010-12-01", "2010-12-02"])[-1],
        prev=(dates or ["2010-12-01", "2010-12-02"])[0],
        dates=dates or [], scope=None, dimensions=[],
    )


class AnalysisAgentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = load_metric_catalog(UCI_CATALOG_PATH)
        self.path = Path("unused.sqlite")

    def test_zero_baseline_has_no_percentage(self) -> None:
        with patch("taobao_agent.daily_metric_tool.query_uci_metric", side_effect=[0, 125]) as query:
            evidence = fetch_metric_series(intent(), self.catalog, uci_db_path=self.path)
        self.assertIsInstance(evidence, MetricSeriesEvidence)
        answer = answer_analysis(evidence)
        self.assertIn("0.00 英镑", answer)
        self.assertIn("1.25 英镑", answer)
        self.assertIn("基期金额为 0，变化率无法计算", answer)
        self.assertEqual(query.call_count, 2)

    def test_three_dates_use_one_evidence_collection(self) -> None:
        dates = ["2010-12-01", "2010-12-02", "2010-12-03"]
        with patch("taobao_agent.daily_metric_tool.query_uci_metric", side_effect=[10000, 8000, 9000]) as query:
            evidence = fetch_metric_series(intent(dates), self.catalog, uci_db_path=self.path)
        self.assertIsInstance(evidence, MetricSeriesEvidence)
        self.assertEqual(query.call_count, 3)
        answer = answer_analysis(evidence)
        self.assertIn("2010-12-03 为 90.00 英镑", answer)
        self.assertIn("首末下降 10.00 英镑，变化率 10.00%", answer)
        self.assertIn("2010-12-01→2010-12-02 下降 20.00 英镑", answer)
        self.assertIn("2010-12-02→2010-12-03 上升 10.00 英镑", answer)
        self.assertIn("不能判断变化原因", answer)

    def test_missing_dates_or_dimension_does_not_query(self) -> None:
        with patch("taobao_agent.daily_metric_tool.query_uci_metric") as query:
            missing = fetch_metric_series(
                intent().model_copy(update={"prev": None, "dates": []}), self.catalog, uci_db_path=self.path
            )
            grouped = fetch_metric_series(
                intent().model_copy(update={"dimensions": ["国家"]}),
                self.catalog, uci_db_path=self.path,
            )
        self.assertIn("至少两个不同", missing)
        self.assertIn("分组", grouped)
        query.assert_not_called()

    def test_later_query_failure_does_not_leak_partial_amounts(self) -> None:
        with patch(
            "taobao_agent.daily_metric_tool.query_uci_metric",
            side_effect=[123456, 654321, UCIQueryError("private database path")],
        ):
            answer = fetch_metric_series(intent([
                "2010-12-01", "2010-12-02", "2010-12-03"
            ]), self.catalog, uci_db_path=self.path)
        self.assertIn("没有返回数值", answer)
        self.assertNotIn("1234.56", answer)
        self.assertNotIn("private database path", answer)

    def test_out_of_range_reports_coverage_without_amount(self) -> None:
        with patch(
            "taobao_agent.daily_metric_tool.query_uci_metric",
            side_effect=UCIDateOutOfRange("2010-12-01", "2011-12-09"),
        ):
            answer = fetch_metric_series(intent(), self.catalog, uci_db_path=self.path)
        self.assertIn("2010-12-01 至 2011-12-09", answer)
        self.assertIn("没有返回数值", answer)

    def test_evidence_rejects_mixed_metric_and_out_of_order_dates(self) -> None:
        first = DailyMetricValue(metric_name="交易明细金额", business_date="2010-12-01", amount_minor=10000, source="uci", unit="pence")
        wrong = DailyMetricValue(metric_name="GMV", business_date="2010-12-02", amount_minor=8000, source="uci", unit="pence")
        with self.assertRaises(ValidationError):
            MetricSeriesEvidence(metric_name="交易明细金额", values=(first, wrong))
        with self.assertRaises(ValidationError):
            MetricSeriesEvidence(metric_name="交易明细金额", values=(wrong, first))


if __name__ == "__main__":
    unittest.main()
