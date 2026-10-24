"""Check the boundary for reading user supplied metric definitions."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from taobao_agent.metric_catalog import load_metric_catalog


class MetricCatalogTest(unittest.TestCase):
    def test_duplicate_names_are_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "metrics.json"
            path.write_text(
                '[{"name":"GMV","table":"a","field":"x","source":"one"},'
                '{"name":"gmv","table":"b","field":"y","source":"two"}]',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "重复名称"):
                load_metric_catalog(path)

    def test_missing_file_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "missing.json"

            with self.assertRaisesRegex(ValueError, "无法读取指标目录文件"):
                load_metric_catalog(path)

    def test_invalid_calculation_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "metrics.json"
            for calculation in (
                {
                    "aggregation": "average",
                    "date_field": "paid_date",
                    "condition": {"field": "payment_status", "value": "paid"},
                    "unit": "fen",
                },
                {
                    "aggregation": "sum",
                    "condition": {"field": "payment_status", "value": "paid"},
                    "unit": "fen",
                },
                {
                    "aggregation": "sum",
                    "date_field": "paid_date",
                    "condition": {"field": "", "value": "paid"},
                    "unit": "fen",
                },
                {
                    "aggregation": "sum",
                    "date_field": "paid_date",
                    "condition": {"field": "payment_status", "value": "paid", "sql": "1=1"},
                    "unit": "fen",
                },
            ):
                with self.subTest(calculation=calculation):
                    path.write_text(
                        json.dumps(
                            [
                                {
                                    "name": "GMV",
                                    "table": "demo_orders",
                                    "field": "order_amount_fen",
                                    "source": "test fixture",
                                    "calculation": calculation,
                                }
                            ]
                        ),
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(ValueError, "指标目录格式无效"):
                        load_metric_catalog(path)
