"""New multi-table, memory, expert-tool and HTTP contract checks."""

import json
import sqlite3
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from taobao_agent.commerce_data import SOURCE_ID, CommerceDataError, seed_commerce_db, verify_etl
from taobao_agent.memory import recall, remember
from taobao_agent.web.api import create_app
from taobao_agent.workflow import run_workflow
from test_commerce_workflow import FakeModel, intent_payload


class Lessons3136Test(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.database = self.root / "commerce.sqlite"
        seed_commerce_db(self.database)

    def test_multitable_etl_filters_cancelled_and_reconciles_daily(self):
        self.assertEqual(SOURCE_ID, "synthetic-commerce-v2")
        with closing(sqlite3.connect(self.database)) as connection:
            paid = connection.execute(
                "SELECT SUM(pay_amount_cents) FROM fact_order WHERE order_status='paid'"
            ).fetchone()[0]
            cancelled = connection.execute(
                "SELECT COUNT(*),SUM(pay_amount_cents) FROM fact_order "
                "WHERE order_status='cancelled'"
            ).fetchone()
            daily = connection.execute(
                "SELECT SUM(pay_amount_cents) FROM commerce_daily"
            ).fetchone()[0]
            joined = connection.execute(
                "SELECT COUNT(*) FROM fact_visit_daily v "
                "JOIN dim_product p ON v.product_id=p.product_id "
                "JOIN dim_activity a ON v.activity_id=a.activity_id"
            ).fetchone()[0]
        self.assertEqual(paid, daily)
        self.assertEqual(cancelled, (1, 999999))
        self.assertEqual(joined, 10)

    def test_lineage_names_all_upstream_tables_and_filter(self):
        model = FakeModel(intent_payload("lineage"))
        result = run_workflow("GMV 从哪里来？", model, date(2026, 11, 2),
                              database_path=self.database, record_path=None)
        for phrase in ("fact_order.pay_amount_cents", "order_status=paid",
                       "dim_product", "dim_activity", "commerce_daily"):
            self.assertIn(phrase, result.answer)

    def test_upstream_tampering_is_caught_by_quality_gate(self):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "UPDATE fact_order SET pay_amount_cents=pay_amount_cents+1 "
                "WHERE order_id=(SELECT MIN(order_id) FROM fact_order WHERE order_status='paid')"
            )
            connection.commit()
        with self.assertRaises(CommerceDataError):
            verify_etl(self.database)

    def test_analysis_tools_are_logged_without_query_permission(self):
        model = FakeModel(intent_payload(
            "attribution", dates=["2026-10-21", "2026-10-22"], dimensions=["行业"]))
        trace = self.root / "events.jsonl"
        result = run_workflow("按行业分析 2026-10-22 GMV 比 2026-10-21 为什么下降？",
                              model, date(2026, 11, 2), database_path=self.database,
                              record_path=trace)
        self.assertIn("数值贡献", result.answer)
        actions = [json.loads(line)["action"] for line in trace.read_text().splitlines()]
        self.assertIn("funnel_reconcile", actions)
        self.assertIn("segment_contribution", actions)

    def test_memory_is_session_isolated_bounded_and_persistent(self):
        memory = self.root / "sessions.sqlite"
        remember(memory, "alice", "2026-10-20 GMV", "答案 100 元", {"metric_name": "GMV"})
        remember(memory, "alice", "2026-10-21 订单数", "答案 200 单", {"metric_name": "订单数"})
        remember(memory, "alice", "2026-10-22 访客数", "答案 300 人", {"metric_name": "访客数"})
        remember(memory, "alice", "2026-10-22 GMV", "答案 400 元", {"metric_name": "GMV"})
        remember(memory, "bob", "私有问题", "私有答案", {"metric_name": "GMV"})
        history = recall(memory, "alice", "GMV 怎么变了", limit=2, char_budget=1800)
        self.assertIn("2026-10-20 GMV", history)
        self.assertNotIn("私有答案", history)
        self.assertLessEqual(len(recall(memory, "alice", "GMV", char_budget=80)), 80)

    def test_workflow_reuses_verified_history_after_multiple_runs(self):
        checkpoint = self.root / "history.sqlite"
        for day in ("2026-10-20", "2026-10-21", "2026-10-22"):
            model = FakeModel(intent_payload("data_query", dates=[day]))
            run_workflow(f"{day} GMV 多少？", model, date(2026, 11, 2),
                         database_path=self.database, session_id="alice",
                         checkpoint_path=checkpoint, record_path=None)
        self.assertEqual(
            len(recall(checkpoint, "alice", "GMV").splitlines()), 3
        )
        self.assertEqual(recall(checkpoint, "bob", "GMV"), "")

    def test_fastapi_json_and_sse_use_same_workflow(self):
        def factory():
            return FakeModel(intent_payload("data_query", dates=["2026-10-22"]))

        app = create_app(factory, database_path=self.database,
                         checkpoint_path=self.root / "checkpoints.sqlite",
                         record_path=self.root / "events.jsonl",
                         reference_date=date(2026, 11, 2))
        client = TestClient(app)
        self.assertEqual(client.get("/health").json(), {"status": "ok"})
        normal = client.post("/v1/ask", json={"question": "2026-10-22 GMV 多少？"})
        self.assertEqual(normal.status_code, 200)
        self.assertEqual(normal.json()["next_expert"], "query")
        streamed = client.post("/v1/ask/stream", json={"question": "2026-10-22 GMV 多少？"})
        self.assertEqual(streamed.status_code, 200)
        self.assertIn("event: step", streamed.text)
        self.assertIn("event: result", streamed.text)
        self.assertIn("event: done", streamed.text)
        self.assertIn("GMV", streamed.text)
        invalid = client.post("/v1/ask", json={"question": "", "extra": 1})
        self.assertEqual(invalid.status_code, 422)


if __name__ == "__main__":
    unittest.main()
