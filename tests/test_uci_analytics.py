"""End-to-end checks for the enriched public data, gated SQL and group contribution."""

import json
import sqlite3
import unittest
from contextlib import closing
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from langchain_core.messages import AIMessage
from openpyxl import Workbook

from taobao_agent.aggregate_agent import fetch_aggregate_evidence
from taobao_agent.intent_agent import IntentResult
from taobao_agent.metric_sql import AggregateQuery, build_aggregate_query
from taobao_agent.uci_analytics import (
    import_uci_enriched, query_uci_aggregate, source_catalog,
)
from taobao_agent.uci_online_retail import UCIQueryError
from taobao_agent.workflow import run_workflow


class FixedIntentModel:
    def __init__(self, *, intent_type: str, metrics: list[str], dates: list[str],
                 dimension: str | None = None, date_range: dict[str, str] | None = None) -> None:
        self.intent_type = intent_type
        self.metrics = metrics
        self.dates = dates
        self.dimension = dimension
        self.date_range = date_range

    def invoke(self, messages: list[object]) -> AIMessage:
        data = {
            "intent_type": self.intent_type, "secondary_intents": [], "confidence": 0.95,
            "metric_name": self.metrics[0], "metric_field": None, "time_label": None,
            "prev": self.dates[0] if len(self.dates) > 1 else None,
            "curr": self.dates[-1] if self.dates else None,
            "dates": self.dates, "metric_names": self.metrics,
            "date_range": self.date_range, "scope": None,
            "dimensions": [self.dimension] if self.dimension else [],
        }
        return AIMessage(content=json.dumps(data, ensure_ascii=False))


class UCIAnalyticsTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        workbook_path = self.directory / "source.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["InvoiceNo", "StockCode", "Description", "Quantity", "InvoiceDate",
                      "UnitPrice", "CustomerID", "Country"])
        for row in [
            (1, "A", "Item A", 2, datetime(2010, 12, 1), 10, 123, "United Kingdom"),
            (2, "B", "Item B", 1, datetime(2010, 12, 1), 5, 124, "France"),
            (3, "A", "Item A", 1, datetime(2010, 12, 2), 10, 123, "United Kingdom"),
            (4, "B", "Item B", 2, datetime(2010, 12, 2), 5, 124, "France"),
            ("C5", "A", "Item A", -1, datetime(2010, 12, 2), 10, 123, "United Kingdom"),
        ]:
            sheet.append(row)
        workbook.save(workbook_path)
        self.path = self.directory / "enriched.sqlite"
        self.summary = import_uci_enriched(workbook_path, self.path)
        self.catalog = source_catalog(self.path)

    def test_import_is_separate_and_drops_customer_id(self) -> None:
        self.assertEqual((self.summary.positive_rows, self.summary.cancelled_rows), (4, 1))
        self.assertEqual(len(self.catalog), 3)
        with closing(sqlite3.connect(self.path)) as connection:
            columns = [row[1] for row in connection.execute("PRAGMA table_info(retail_lines)")]
            self.assertNotIn("CustomerID", columns)
            self.assertNotIn("customer_id", columns)
            self.assertIn("country", columns)
            self.assertIn("stock_code", columns)
        with self.assertRaises(FileExistsError):
            import_uci_enriched(self.directory / "source.xlsx", self.path)

    def test_multiple_metrics_range_and_group_totals_are_reconciled(self) -> None:
        question = "2010-12-01 至 2010-12-02 按国家看交易明细金额、正向商品件数和正向交易行数"
        for listed_dates in ([], ["2010-12-01", "2010-12-02"]):
            with self.subTest(listed_dates=listed_dates):
                model = FixedIntentModel(
                    intent_type="data_query",
                    metrics=["交易明细金额", "正向商品件数", "正向交易行数"],
                    dates=listed_dates, dimension="国家",
                    date_range={"start": "2010-12-01", "end": "2010-12-02"},
                )
                result = run_workflow(question, model, date(2026, 10, 31), self.catalog, self.path)
                self.assertEqual(result.next_expert, "query")
                for expected in ("45.00 英镑", "6 件", "4 行", "France", "United Kingdom"):
                    self.assertIn(expected, result.answer)
        for metric in self.catalog:
            grouped = query_uci_aggregate(metric, date(2010, 12, 1), date(2010, 12, 2), self.path, "国家")
            overall = query_uci_aggregate(metric, date(2010, 12, 1), date(2010, 12, 2), self.path)
            self.assertEqual(grouped.total, overall.total)
            self.assertEqual(sum(item.value for item in grouped.groups), overall.total)

    def test_group_analysis_uses_query_evidence_and_does_not_claim_cause(self) -> None:
        question = "2010-12-02 交易明细金额比 2010-12-01 为什么下降？按国家拆解"
        model = FixedIntentModel(
            intent_type="attribution", metrics=["交易明细金额"],
            dates=["2010-12-01", "2010-12-02"], dimension="国家",
        )
        result = run_workflow(question, model, date(2026, 10, 31), self.catalog, self.path)
        self.assertEqual(result.next_expert, "analysis")
        self.assertIn("下降 5.00 英镑", result.answer)
        self.assertIn("United Kingdom减少 10.00 英镑", result.answer)
        self.assertIn("各上升组共增加 5.00 英镑", result.answer)
        self.assertIn("200.00%", result.answer)
        self.assertIn("不能单凭这些数据证明下降原因", result.answer)

    def test_sql_gate_rejects_changed_statement_and_parameters(self) -> None:
        metric = self.catalog[0]
        day = date(2010, 12, 1)
        approved = build_aggregate_query(metric, day, day, "国家")
        for changed in (
            AggregateQuery(approved.statement.replace("SUM", "MAX"), approved.parameters),
            AggregateQuery(approved.statement, ("2010-12-02", "2010-12-02", "positive")),
        ):
            with self.subTest(changed=changed), self.assertRaises(UCIQueryError):
                query_uci_aggregate(metric, day, day, self.path, "国家", candidate=changed)
        with self.assertRaises(UCIQueryError):
            query_uci_aggregate(metric, day, day, self.path, "客户ID")
        product_groups = query_uci_aggregate(metric, day, day, self.path, "商品编码")
        self.assertEqual({item.group: item.value for item in product_groups.groups}, {"A": 2000, "B": 500})
        with self.assertRaises(UCIQueryError):
            query_uci_aggregate(metric, day, date(2011, 1, 1), self.path)

    def test_invalid_request_does_not_return_partial_data(self) -> None:
        intent = IntentResult(
            intent_type="data_query", secondary_intents=[], confidence=0.9,
            metric_name="交易明细金额", metric_names=["交易明细金额", "不存在的指标"],
            metric_field=None, time_label=None, curr="2010-12-02", prev=None,
            dimensions=["国家"], scope=None,
        )
        result = fetch_aggregate_evidence(intent, "2010-12-02 查询", self.catalog, self.path)
        self.assertIsInstance(result, str)
        self.assertNotIn("20.00", result)


if __name__ == "__main__":
    unittest.main()
