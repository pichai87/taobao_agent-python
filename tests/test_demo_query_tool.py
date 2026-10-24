"""Verify the synthetic data tool's gated result and read-only connection."""

import sqlite3
import unittest
from contextlib import closing
from datetime import date

from taobao_agent.demo_query_tool import (
    DEMO_CATALOG_PATH,
    DemoQueryError,
    _open_demo_database,
    query_demo_metric,
)
from taobao_agent.metric_catalog import load_metric_catalog
from taobao_agent.metric_search import MetricDefinition


class DemoQueryToolTest(unittest.TestCase):
    def setUp(self) -> None:
        self.metric = load_metric_catalog(DEMO_CATALOG_PATH)[0]

    def test_known_day_and_empty_day(self) -> None:
        self.assertEqual(query_demo_metric(self.metric, date(2026, 10, 22)), 20000)
        self.assertEqual(query_demo_metric(self.metric, date(2026, 10, 20)), 0)

    def test_database_connection_rejects_writes_after_seeding(self) -> None:
        with closing(_open_demo_database()) as connection:
            self.assertEqual(connection.execute("PRAGMA query_only").fetchone()[0], 1)
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute(
                    "INSERT INTO demo_orders VALUES (?, ?, ?, ?, ?)",
                    ("O999", "2026-10-22", "paid", "手机", 100),
                )

    def test_unapproved_table_is_not_queried(self) -> None:
        other_metric = MetricDefinition(
            name="GMV",
            table="other_orders",
            field=self.metric.field,
            source="test",
            calculation=self.metric.calculation,
        )
        with self.assertRaises(DemoQueryError):
            query_demo_metric(other_metric, date(2026, 10, 22))


if __name__ == "__main__":
    unittest.main()
