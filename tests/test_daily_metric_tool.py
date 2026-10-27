"""Check the callable UCI tool's public arguments and source restrictions."""

import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from taobao_agent.daily_metric_tool import (
    DailyMetricValue,
    MetricNotApprovedError,
    MetricNotFoundError,
    make_daily_metric_tool,
)
from taobao_agent.metric_catalog import load_metric_catalog
from taobao_agent.uci_online_retail import UCI_CATALOG_PATH


class DailyMetricToolTest(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = load_metric_catalog(UCI_CATALOG_PATH)
        self.database_path = Path("unused.sqlite")

    def test_only_metric_and_date_are_public_tool_arguments(self) -> None:
        daily_tool = make_daily_metric_tool(self.catalog, uci_db_path=self.database_path)

        self.assertEqual(daily_tool.name, "query_daily_metric")
        self.assertEqual(set(daily_tool.args), {"metric_name", "business_date"})
        with self.assertRaises(ValidationError):
            daily_tool.invoke({
                "metric_name": "交易明细金额", "business_date": "2010-12-01",
                "database_path": "other.sqlite",
            })

    def test_tool_returns_structured_uci_amount(self) -> None:
        daily_tool = make_daily_metric_tool(self.catalog, uci_db_path=self.database_path)
        with patch("taobao_agent.daily_metric_tool.query_uci_metric", return_value=635) as query:
            value = DailyMetricValue.model_validate(daily_tool.invoke({
                "metric_name": "交易明细金额", "business_date": "2010-12-01",
            }))

        self.assertEqual(value.amount_minor, 635)
        self.assertEqual(value.source, "uci")
        self.assertEqual(value.unit, "pence")
        query.assert_called_once_with(self.catalog[0], date(2010, 12, 1), self.database_path)

    def test_unknown_and_changed_metric_never_reach_database(self) -> None:
        daily_tool = make_daily_metric_tool(self.catalog, uci_db_path=self.database_path)
        changed_catalog = [self.catalog[0].model_copy(update={"source": "unverified"})]
        changed_tool = make_daily_metric_tool(changed_catalog, uci_db_path=self.database_path)

        with patch("taobao_agent.daily_metric_tool.query_uci_metric") as database_query:
            with self.assertRaises(MetricNotFoundError):
                daily_tool.invoke({"metric_name": "客单价", "business_date": "2010-12-01"})
            with self.assertRaises(MetricNotApprovedError):
                changed_tool.invoke({"metric_name": "交易明细金额", "business_date": "2010-12-01"})
        database_query.assert_not_called()

    def test_database_path_must_be_bound_by_program(self) -> None:
        with self.assertRaises(TypeError):
            make_daily_metric_tool(self.catalog)


if __name__ == "__main__":
    unittest.main()
