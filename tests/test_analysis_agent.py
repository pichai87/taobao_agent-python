"""Business checks for two-date comparisons and incomplete evidence."""

import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from taobao_agent.analysis_agent import answer_analysis
from taobao_agent.demo_query_tool import DEMO_CATALOG_PATH
from taobao_agent.intent_agent import IntentResult
from taobao_agent.metric_catalog import load_metric_catalog
from taobao_agent.query_agent import QueryEvidence, fetch_comparison
from taobao_agent.uci_online_retail import UCI_CATALOG_PATH, UCIDateOutOfRange, UCIQueryError


def intent(metric_name: str = "交易明细金额") -> IntentResult:
    return IntentResult(
        intent_type="attribution", secondary_intents=[], confidence=0.9,
        metric_name=metric_name, metric_field=None, time_label="两天",
        curr="2010-12-02", prev="2010-12-01", scope=None, dimensions=[],
    )


class AnalysisAgentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = load_metric_catalog(UCI_CATALOG_PATH)
        self.path = Path("unused.sqlite")

    def test_zero_baseline_has_no_percentage(self) -> None:
        with patch("taobao_agent.query_agent.query_uci_metric", side_effect=[0, 125]) as query:
            evidence = fetch_comparison(intent(), self.catalog, uci_db_path=self.path)
        self.assertIsInstance(evidence, QueryEvidence)
        answer = answer_analysis(evidence)
        self.assertIn("0.00 英镑", answer)
        self.assertIn("1.25 英镑", answer)
        self.assertIn("基期金额为 0，变化率无法计算", answer)
        self.assertEqual(query.call_count, 2)

    def test_missing_date_or_dimension_does_not_query(self) -> None:
        with patch("taobao_agent.query_agent.query_uci_metric") as query:
            missing = fetch_comparison(
                intent().model_copy(update={"prev": None}), self.catalog, uci_db_path=self.path
            )
            grouped = fetch_comparison(
                intent().model_copy(update={"dimensions": ["国家"]}),
                self.catalog, uci_db_path=self.path,
            )
        self.assertIn("两个不同的明确日期", missing)
        self.assertIn("不支持指定范围、分组", grouped)
        query.assert_not_called()

    def test_second_query_failure_does_not_leak_first_amount(self) -> None:
        with patch(
            "taobao_agent.query_agent.query_uci_metric",
            side_effect=[123456, UCIQueryError("private database path")],
        ):
            answer = fetch_comparison(intent(), self.catalog, uci_db_path=self.path)
        self.assertIn("没有返回比较结果", answer)
        self.assertNotIn("1234.56", answer)
        self.assertNotIn("private database path", answer)

    def test_out_of_range_reports_coverage_without_amount(self) -> None:
        with patch(
            "taobao_agent.query_agent.query_uci_metric",
            side_effect=UCIDateOutOfRange("2010-12-01", "2011-12-09"),
        ):
            answer = fetch_comparison(intent(), self.catalog, uci_db_path=self.path)
        self.assertIn("2010-12-01 至 2011-12-09", answer)
        self.assertIn("没有返回比较结果", answer)

    def test_demo_comparison_uses_real_synthetic_orders(self) -> None:
        demo_intent = intent("GMV").model_copy(
            update={"prev": date(2026, 10, 21), "curr": date(2026, 10, 22)}
        )
        evidence = fetch_comparison(
            demo_intent, load_metric_catalog(DEMO_CATALOG_PATH), demo_data=True
        )
        self.assertIsInstance(evidence, QueryEvidence)
        answer = answer_analysis(evidence)
        self.assertIn("300.00 元", answer)
        self.assertIn("200.00 元", answer)
        self.assertIn("下降 100.00 元，变化率 33.33%", answer)
        self.assertIn("不能据此判断", answer)


if __name__ == "__main__":
    unittest.main()
