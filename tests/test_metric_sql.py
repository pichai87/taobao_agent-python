"""Check the small SQL template and its post-build gate on synthetic data."""

import sqlite3
import unittest
from contextlib import closing
from dataclasses import replace
from datetime import date
from pathlib import Path

from taobao_agent.metric_catalog import load_metric_catalog
from taobao_agent.metric_search import EqualsCondition, MetricCalculation, MetricDefinition
from taobao_agent.metric_sql import build_metric_query, validate_metric_query


EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


class MetricSqlTest(unittest.TestCase):
    def setUp(self) -> None:
        self.metric = load_metric_catalog(EXAMPLES / "demo_metrics.json")[0]
        self.business_date = date(2026, 10, 22)
        self.approved_columns = {
            "demo_orders": {
                "order_id",
                "paid_date",
                "payment_status",
                "category",
                "order_amount_fen",
            }
        }

    def test_validated_query_returns_known_gmv(self) -> None:
        candidate = build_metric_query(self.metric, self.business_date)
        approved = validate_metric_query(
            candidate, self.metric, self.business_date, self.approved_columns
        )

        self.assertEqual(approved.parameters, ("2026-10-22", "paid"))
        self.assertEqual(approved.statement.count("?"), 2)
        with closing(sqlite3.connect(":memory:")) as connection:
            connection.executescript((EXAMPLES / "demo_shop.sql").read_text(encoding="utf-8"))
            value = connection.execute(approved.statement, approved.parameters).fetchone()[0]
        self.assertEqual(value, 20000)

    def test_empty_date_returns_zero(self) -> None:
        day = date(2026, 10, 20)
        candidate = build_metric_query(self.metric, day)
        approved = validate_metric_query(candidate, self.metric, day, self.approved_columns)

        with closing(sqlite3.connect(":memory:")) as connection:
            connection.executescript((EXAMPLES / "demo_shop.sql").read_text(encoding="utf-8"))
            value = connection.execute(approved.statement, approved.parameters).fetchone()[0]
        self.assertEqual(value, 0)

    def test_gate_rejects_changed_statement_and_parameters(self) -> None:
        candidate = build_metric_query(self.metric, self.business_date)
        for altered in (
            replace(candidate, statement="DELETE FROM demo_orders"),
            replace(candidate, statement=candidate.statement + "; DROP TABLE demo_orders"),
            replace(candidate, parameters=("2026-10-21", "paid")),
            replace(candidate, parameters=("2026-10-22", "cancelled")),
        ):
            with self.subTest(altered=altered):
                with self.assertRaisesRegex(ValueError, "SQL 或参数"):
                    validate_metric_query(
                        altered, self.metric, self.business_date, self.approved_columns
                    )

    def test_gate_rejects_unapproved_table_or_field(self) -> None:
        candidate = build_metric_query(self.metric, self.business_date)
        for schema in ({}, {"demo_orders": {"paid_date", "payment_status"}}):
            with self.subTest(schema=schema):
                with self.assertRaisesRegex(ValueError, "未获批准"):
                    validate_metric_query(candidate, self.metric, self.business_date, schema)

    def test_condition_value_is_bound_instead_of_added_to_sql(self) -> None:
        suspicious_value = "paid' OR 1=1 --"
        metric = MetricDefinition(
            name="GMV",
            table="demo_orders",
            field="order_amount_fen",
            source="test",
            calculation=MetricCalculation(
                aggregation="sum",
                date_field="paid_date",
                condition=EqualsCondition(field="payment_status", value=suspicious_value),
                unit="fen",
            ),
        )
        candidate = build_metric_query(metric, self.business_date)
        approved = validate_metric_query(
            candidate, metric, self.business_date, self.approved_columns
        )

        self.assertNotIn(suspicious_value, approved.statement)
        self.assertEqual(approved.parameters[1], suspicious_value)
        with closing(sqlite3.connect(":memory:")) as connection:
            connection.executescript((EXAMPLES / "demo_shop.sql").read_text(encoding="utf-8"))
            value = connection.execute(approved.statement, approved.parameters).fetchone()[0]
        self.assertEqual(value, 0)

    def test_builder_rejects_missing_rule_and_unsafe_identifier(self) -> None:
        no_rule = MetricDefinition(
            name="GMV", table="demo_orders", field="order_amount_fen", source="test"
        )
        with self.assertRaisesRegex(ValueError, "缺少计算规则"):
            build_metric_query(no_rule, self.business_date)

        unsafe = MetricDefinition(
            name="GMV",
            table="demo_orders;DROP",
            field="order_amount_fen",
            source="test",
            calculation=MetricCalculation(
                aggregation="sum",
                date_field="paid_date",
                condition=EqualsCondition(field="payment_status", value="paid"),
                unit="fen",
            ),
        )
        with self.assertRaisesRegex(ValueError, "不支持的表或字段标识符"):
            build_metric_query(unsafe, self.business_date)


if __name__ == "__main__":
    unittest.main()
