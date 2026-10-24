"""Verify the small synthetic business data and its stated GMV convention."""

import sqlite3
import unittest
from contextlib import closing
from pathlib import Path

from taobao_agent.metric_catalog import load_metric_catalog


EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


class DemoDataTest(unittest.TestCase):
    def test_gmv_counts_only_paid_orders_on_the_payment_date(self) -> None:
        with closing(sqlite3.connect(":memory:")) as connection:
            connection.executescript((EXAMPLES / "demo_shop.sql").read_text(encoding="utf-8"))
            rows = connection.execute(
                """SELECT paid_date, SUM(order_amount_fen)
                   FROM demo_orders
                   WHERE payment_status = 'paid'
                   GROUP BY paid_date
                   ORDER BY paid_date"""
            ).fetchall()

        self.assertEqual(
            rows,
            [("2026-10-21", 30000), ("2026-10-22", 20000), ("2026-10-23", 9000)],
        )

    def test_metric_catalog_points_to_the_demo_table_and_amount(self) -> None:
        metrics = load_metric_catalog(EXAMPLES / "demo_metrics.json")

        self.assertEqual(len(metrics), 1)
        metric = metrics[0]
        self.assertEqual(metric.name, "GMV")
        self.assertEqual(metric.table, "demo_orders")
        self.assertEqual(metric.field, "order_amount_fen")
        self.assertIsNotNone(metric.calculation)
        self.assertEqual(metric.calculation.aggregation, "sum")
        self.assertEqual(metric.calculation.date_field, "paid_date")
        self.assertEqual(metric.calculation.condition.field, "payment_status")
        self.assertEqual(metric.calculation.condition.value, "paid")
        self.assertEqual(metric.calculation.unit, "fen")

        with closing(sqlite3.connect(":memory:")) as connection:
            connection.executescript((EXAMPLES / "demo_shop.sql").read_text(encoding="utf-8"))
            columns = {row[1] for row in connection.execute("PRAGMA table_info(demo_orders)")}
        self.assertTrue(
            {metric.field, metric.calculation.date_field, metric.calculation.condition.field}
            <= columns
        )


if __name__ == "__main__":
    unittest.main()
