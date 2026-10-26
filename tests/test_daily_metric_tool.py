"""Check the callable tool's public arguments and source restrictions."""

import unittest
from unittest.mock import patch

from pydantic import ValidationError

from taobao_agent.daily_metric_tool import (
    DailyMetricValue,
    MetricNotApprovedError,
    MetricNotFoundError,
    make_daily_metric_tool,
)
from taobao_agent.demo_query_tool import DEMO_CATALOG_PATH
from taobao_agent.metric_catalog import load_metric_catalog


class DailyMetricToolTest(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = load_metric_catalog(DEMO_CATALOG_PATH)

    def test_only_metric_and_date_are_public_tool_arguments(self) -> None:
        daily_tool = make_daily_metric_tool(self.catalog, demo_data=True)

        self.assertEqual(daily_tool.name, "query_daily_metric")
        self.assertEqual(set(daily_tool.args), {"metric_name", "business_date"})
        with self.assertRaises(ValidationError):
            daily_tool.invoke({
                "metric_name": "GMV", "business_date": "2026-10-22",
                "database_path": "other.sqlite",
            })

    def test_tool_returns_structured_synthetic_amount(self) -> None:
        daily_tool = make_daily_metric_tool(self.catalog, demo_data=True)

        value = DailyMetricValue.model_validate(daily_tool.invoke({
            "metric_name": "GMV", "business_date": "2026-10-22",
        }))

        self.assertEqual(value.amount_minor, 20000)
        self.assertEqual(value.source, "demo")
        self.assertEqual(value.unit, "fen")

    def test_unknown_and_changed_metric_never_reach_database(self) -> None:
        daily_tool = make_daily_metric_tool(self.catalog, demo_data=True)
        changed_catalog = [self.catalog[0].model_copy(update={"source": "unverified"})]
        changed_tool = make_daily_metric_tool(changed_catalog, demo_data=True)

        with patch("taobao_agent.daily_metric_tool.query_demo_metric") as database_query:
            with self.assertRaises(MetricNotFoundError):
                daily_tool.invoke({"metric_name": "客单价", "business_date": "2026-10-22"})
            with self.assertRaises(MetricNotApprovedError):
                changed_tool.invoke({"metric_name": "GMV", "business_date": "2026-10-22"})
        database_query.assert_not_called()

    def test_source_must_be_explicit_and_unique(self) -> None:
        with self.assertRaises(ValueError):
            make_daily_metric_tool(self.catalog)
        with self.assertRaises(ValueError):
            make_daily_metric_tool(
                self.catalog, demo_data=True, uci_db_path=DEMO_CATALOG_PATH
            )


if __name__ == "__main__":
    unittest.main()
