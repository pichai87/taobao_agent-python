"""Check public-data import, metric execution and source boundaries."""

import sqlite3
import unittest
from contextlib import closing
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from openpyxl import Workbook

from taobao_agent.metric_catalog import load_metric_catalog
from taobao_agent.metric_sql import MetricQuery, build_metric_query
from taobao_agent.uci_online_retail import (
    UCI_CATALOG_PATH,
    UCIDateOutOfRange,
    UCIQueryError,
    import_uci_online_retail,
    query_uci_metric,
)


class UCIOnlineRetailTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.workbook_path = self.directory / "sample.xlsx"
        self.database_path = self.directory / "retail.sqlite"
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(
            ["InvoiceNo", "StockCode", "Description", "Quantity", "InvoiceDate",
             "UnitPrice", "CustomerID", "Country"]
        )
        rows = [
            (1, 100, "A", 2, datetime(2010, 12, 1, 8), 2.55, None, "UK"),
            (2, 101, "B", 1, datetime(2010, 12, 1, 9), 1.25, None, "UK"),
            ("C3", 100, "A", -1, datetime(2010, 12, 1, 10), 2.55, None, "UK"),
            (4, 102, "C", 1, datetime(2010, 12, 1, 11), 0, None, "UK"),
            (5, 103, "D", 1, datetime(2010, 12, 2, 8), 3.00, None, "UK"),
        ]
        for row in rows:
            sheet.append(row)
        workbook.save(self.workbook_path)
        self.metric = load_metric_catalog(UCI_CATALOG_PATH)[0]

    def test_import_and_query_use_only_positive_lines(self) -> None:
        summary = import_uci_online_retail(self.workbook_path, self.database_path)

        self.assertEqual(
            (summary.positive_rows, summary.cancelled_rows, summary.other_rows),
            (3, 1, 1),
        )
        self.assertEqual((summary.first_date, summary.last_date), ("2010-12-01", "2010-12-02"))
        self.assertEqual(query_uci_metric(self.metric, date(2010, 12, 1), self.database_path), 635)
        self.assertEqual(query_uci_metric(self.metric, date(2010, 12, 2), self.database_path), 300)
        with closing(sqlite3.connect(self.database_path)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM retail_lines").fetchone()[0], 5)
            self.assertEqual(
                connection.execute("PRAGMA table_info(retail_lines)").fetchall()[-1][1],
                "line_amount_pence",
            )

    def test_out_of_range_date_does_not_look_like_zero_sales(self) -> None:
        import_uci_online_retail(self.workbook_path, self.database_path)

        with self.assertRaises(UCIDateOutOfRange):
            query_uci_metric(self.metric, date(2026, 10, 22), self.database_path)

    def test_existing_database_is_not_overwritten(self) -> None:
        self.database_path.write_text("existing file", encoding="utf-8")

        with self.assertRaises(FileExistsError):
            import_uci_online_retail(self.workbook_path, self.database_path)
        self.assertEqual(self.database_path.read_text(encoding="utf-8"), "existing file")

    def test_unapproved_rule_and_nonimported_database_are_rejected(self) -> None:
        import_uci_online_retail(self.workbook_path, self.database_path)
        wrong_metric = self.metric.model_copy(update={"field": "invoice_date"})
        with self.assertRaises(UCIQueryError):
            query_uci_metric(wrong_metric, date(2010, 12, 1), self.database_path)

        with self.assertRaises(UCIQueryError):
            query_uci_metric(self.metric, date(2010, 12, 1), self.directory / "missing.sqlite")
        self.assertFalse((self.directory / "missing.sqlite").exists())

    def test_prepared_sql_is_checked_again_at_database_boundary(self) -> None:
        import_uci_online_retail(self.workbook_path, self.database_path)
        day = date(2010, 12, 1)
        approved = build_metric_query(self.metric, day)
        self.assertEqual(
            query_uci_metric(self.metric, day, self.database_path, candidate=approved), 635,
        )
        changed = MetricQuery(approved.statement.replace("SUM", "MAX"), approved.parameters)
        with self.assertRaises(UCIQueryError):
            query_uci_metric(self.metric, day, self.database_path, candidate=changed)
        wrong_day = MetricQuery(approved.statement, ("2010-12-02", "positive"))
        with self.assertRaises(UCIQueryError):
            query_uci_metric(self.metric, day, self.database_path, candidate=wrong_day)


if __name__ == "__main__":
    unittest.main()
