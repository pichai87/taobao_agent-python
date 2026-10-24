"""A local, read-only query tool for the project's synthetic orders only."""

import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path

from taobao_agent.metric_search import MetricDefinition
from taobao_agent.metric_sql import build_metric_query, validate_metric_query


EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "examples"
DEMO_CATALOG_PATH = EXAMPLES_DIR / "demo_metrics.json"
_DEMO_SQL_PATH = EXAMPLES_DIR / "demo_shop.sql"
_DEMO_TABLE = "demo_orders"
_APPROVED_COLUMNS = frozenset({"paid_date", "payment_status", "order_amount_fen"})


class DemoQueryError(Exception):
    """The synthetic data or metric failed a controlled query step."""


def _open_demo_database() -> sqlite3.Connection:
    """Seed a new in-memory database, then disallow writes on its connection."""

    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(_DEMO_SQL_PATH.read_text(encoding="utf-8"))
        connection.execute("PRAGMA query_only = ON")
        return connection
    except BaseException:
        connection.close()
        raise


def query_demo_metric(metric: MetricDefinition, business_date: date) -> int:
    """Run one gated sum query against the fixed synthetic data."""

    try:
        with closing(_open_demo_database()) as connection:
            actual_columns = frozenset(
                row[1] for row in connection.execute('PRAGMA table_info("demo_orders")')
            )
            approved_columns = {_DEMO_TABLE: actual_columns & _APPROVED_COLUMNS}
            candidate = build_metric_query(metric, business_date)
            approved = validate_metric_query(
                candidate, metric, business_date, approved_columns
            )
            row = connection.execute(approved.statement, approved.parameters).fetchone()
            if row is None or not isinstance(row[0], int):
                raise DemoQueryError("教学数据查询没有返回整数金额")
            return row[0]
    except (OSError, UnicodeError, sqlite3.Error, ValueError) as exc:
        raise DemoQueryError("教学数据查询未通过校验或执行失败") from exc
