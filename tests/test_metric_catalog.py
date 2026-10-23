"""Check the boundary for reading user supplied metric definitions."""

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
