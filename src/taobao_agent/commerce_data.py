"""Versioned synthetic commerce fixture and its single read-only SQLite executor."""

import argparse
import csv
import hashlib
import json
import os
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from time import monotonic

from taobao_agent.metric_catalog import load_metric_catalog
from taobao_agent.metric_search import MetricDefinition
from taobao_agent.metric_sql import MetricQuery, build_metric_query
from taobao_agent.agents.sql_validator_gate import validate_metric_query
from taobao_agent.tools.etl import DAILY_ETL_SELECT, DAILY_ETL_SQL


SOURCE_ID = "synthetic-commerce-v2"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = PROJECT_ROOT / "examples" / "commerce_daily.csv"
CATALOG_PATH = PROJECT_ROOT / "examples" / "commerce_metrics.json"
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "commerce" / "commerce_v2.sqlite"
_COLUMNS = frozenset({
    "biz_date", "industry", "activity_type", "visitor_uv", "order_count", "pay_amount_cents",
})
_HEADERS = ("biz_date", "industry", "activity_type", "visitor_uv", "order_count", "pay_amount_cents")


class CommerceDataError(ValueError):
    """The synthetic source, schema or bounded query is invalid."""


@dataclass(frozen=True)
class SeedSummary:
    source_id: str
    fixture_sha256: str
    rows: int
    first_date: str
    last_date: str


@dataclass(frozen=True)
class MetricResult:
    metric_name: str
    start: date
    end: date
    group_by: str | None
    total: int
    groups: tuple[tuple[str, int], ...]
    unit: str
    source: str = SOURCE_ID


def _fixture_hash() -> str:
    return hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest()


def seed_commerce_db(database_path: Path = DEFAULT_DB_PATH) -> SeedSummary:
    """Create one reproducible data source; never overwrite an existing file."""

    if database_path.exists():
        raise FileExistsError(f"目标数据库已存在，不会覆盖：{database_path}")
    with FIXTURE_PATH.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if tuple(reader.fieldnames or ()) != _HEADERS:
            raise CommerceDataError("合成数据字段与批准结构不一致")
        rows: list[tuple[str, str, str, int, int, int]] = []
        seen: set[tuple[str, str, str]] = set()
        for line, item in enumerate(reader, start=2):
            try:
                day = date.fromisoformat(item["biz_date"])
                if day.isoformat() != item["biz_date"]:
                    raise ValueError
                industry = item["industry"].strip()
                activity = item["activity_type"].strip()
                visitors = int(item["visitor_uv"])
                orders = int(item["order_count"])
                amount = int(item["pay_amount_cents"])
                if not industry or not activity or not 0 <= orders <= visitors or amount < 0:
                    raise ValueError
                key = (day.isoformat(), industry, activity)
                if key in seen:
                    raise ValueError
            except (KeyError, TypeError, ValueError) as exc:
                raise CommerceDataError(f"合成数据第 {line} 行无效") from exc
            seen.add(key)
            rows.append((day.isoformat(), industry, activity, visitors, orders, amount))
    if not rows:
        raise CommerceDataError("合成数据为空")
    database_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(database_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise FileExistsError(f"目标数据库已存在，不会覆盖：{database_path}") from exc
    os.close(descriptor)
    try:
        with closing(sqlite3.connect(database_path)) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(
                "CREATE TABLE dim_product (product_id TEXT PRIMARY KEY, industry TEXT NOT NULL, "
                "product_name TEXT NOT NULL);"
                "CREATE TABLE dim_activity (activity_id TEXT PRIMARY KEY, "
                "activity_type TEXT NOT NULL UNIQUE);"
                "CREATE TABLE fact_visit_daily (biz_date TEXT NOT NULL, product_id TEXT NOT NULL, "
                "activity_id TEXT NOT NULL, visitor_uv INTEGER NOT NULL CHECK (visitor_uv >= 0), "
                "PRIMARY KEY (biz_date, product_id, activity_id), "
                "FOREIGN KEY (product_id) REFERENCES dim_product(product_id), "
                "FOREIGN KEY (activity_id) REFERENCES dim_activity(activity_id));"
                "CREATE TABLE fact_order (order_id TEXT PRIMARY KEY, biz_date TEXT NOT NULL, "
                "product_id TEXT NOT NULL, activity_id TEXT NOT NULL, "
                "order_status TEXT NOT NULL CHECK (order_status IN ('paid', 'cancelled')), "
                "pay_amount_cents INTEGER NOT NULL CHECK (pay_amount_cents >= 0), "
                "FOREIGN KEY (product_id) REFERENCES dim_product(product_id), "
                "FOREIGN KEY (activity_id) REFERENCES dim_activity(activity_id));"
                "CREATE TABLE commerce_daily ("
                "biz_date TEXT NOT NULL, industry TEXT NOT NULL, activity_type TEXT NOT NULL, "
                "visitor_uv INTEGER NOT NULL CHECK (visitor_uv >= 0), "
                "order_count INTEGER NOT NULL CHECK (order_count >= 0), "
                "pay_amount_cents INTEGER NOT NULL CHECK (pay_amount_cents >= 0), "
                "PRIMARY KEY (biz_date, industry, activity_type));"
                "CREATE TABLE import_metadata (source_id TEXT NOT NULL, fixture_sha256 TEXT NOT NULL, "
                "first_date TEXT NOT NULL, last_date TEXT NOT NULL);"
            )
            industries = sorted({row[1] for row in rows})
            activities = sorted({row[2] for row in rows})
            product_ids = {name: f"P{index:03d}" for index, name in enumerate(industries, 1)}
            activity_ids = {name: f"A{index:03d}" for index, name in enumerate(activities, 1)}
            connection.executemany(
                "INSERT INTO dim_product VALUES (?, ?, ?)",
                [(product_ids[name], name, f"合成{name}商品") for name in industries],
            )
            connection.executemany(
                "INSERT INTO dim_activity VALUES (?, ?)",
                [(activity_ids[name], name) for name in activities],
            )
            visits = []
            orders = []
            sequence = 0
            for day, industry, activity, visitors, order_count, amount in rows:
                product_id, activity_id = product_ids[industry], activity_ids[activity]
                visits.append((day, product_id, activity_id, visitors))
                if order_count == 0 and amount:
                    raise CommerceDataError("无订单的合成记录不能含支付金额")
                unit, remainder = divmod(amount, order_count) if order_count else (0, 0)
                for index in range(order_count):
                    sequence += 1
                    orders.append((
                        f"O{sequence:06d}", day, product_id, activity_id, "paid",
                        unit + int(index < remainder),
                    ))
            # A cancelled order proves that the ETL status filter is effective.
            first_row = rows[0]
            orders.append((
                "OCANCELLED", first_row[0], product_ids[first_row[1]],
                activity_ids[first_row[2]], "cancelled", 999999,
            ))
            connection.executemany("INSERT INTO fact_visit_daily VALUES (?, ?, ?, ?)", visits)
            connection.executemany("INSERT INTO fact_order VALUES (?, ?, ?, ?, ?, ?)", orders)
            connection.execute(DAILY_ETL_SQL)
            actual = connection.execute(
                "SELECT biz_date, industry, activity_type, visitor_uv, order_count, "
                "pay_amount_cents FROM commerce_daily"
            ).fetchall()
            if sorted(actual) != sorted(rows):
                raise CommerceDataError("多表加工结果与批准的合成样例不一致")
            first, last = min(row[0] for row in rows), max(row[0] for row in rows)
            digest = _fixture_hash()
            connection.execute(
                "INSERT INTO import_metadata VALUES (?, ?, ?, ?)", (SOURCE_ID, digest, first, last)
            )
            connection.commit()
    except BaseException:
        database_path.unlink(missing_ok=True)
        raise
    return SeedSummary(SOURCE_ID, digest, len(rows), first, last)


def _checked_connection(database_path: Path):
    if not database_path.is_file():
        raise CommerceDataError("请先运行 taobao-seed-commerce 创建本地数据库")
    uri = f"{database_path.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        connection.execute("PRAGMA query_only = ON")
        row = connection.execute(
            "SELECT source_id, fixture_sha256, first_date, last_date FROM import_metadata"
        ).fetchone()
        if row is None or row[0] != SOURCE_ID or row[1] != _fixture_hash():
            raise CommerceDataError("数据库来源与当前合成数据版本不一致")
        columns = frozenset(item[1] for item in connection.execute('PRAGMA table_info("commerce_daily")'))
        if not _COLUMNS <= columns:
            raise CommerceDataError("数据库缺少已批准的业务字段")
        required_tables = {"dim_product", "dim_activity", "fact_visit_daily", "fact_order"}
        available_tables = {
            item[0] for item in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if not required_tables <= available_tables:
            raise CommerceDataError("数据库缺少合成数据的上游表")
        return connection, columns, row[2], row[3]
    except BaseException:
        connection.close()
        raise


def inspect_metric(metric: MetricDefinition, database_path: Path) -> tuple[str, str, tuple[str, ...]]:
    """Describe only a catalog metric backed by the actual local schema."""

    if metric not in load_metric_catalog(CATALOG_PATH):
        raise CommerceDataError("指标未在合成电商目录中批准")
    try:
        connection, columns, first, last = _checked_connection(database_path)
        with closing(connection):
            calculation = metric.calculation
            required = {metric.field, calculation.date_field, calculation.condition.field}
            if not required <= columns:
                raise CommerceDataError("数据库字段与指标口径不一致")
            return first, last, tuple(sorted(required))
    except (OSError, sqlite3.Error) as exc:
        raise CommerceDataError("数据库结构检查失败") from exc


def verify_etl(database_path: Path) -> None:
    """Recompute the reviewed join and reject drift from the materialized daily table."""

    connection, _, _, _ = _checked_connection(database_path)
    try:
        with closing(connection):
            derived = connection.execute(DAILY_ETL_SELECT).fetchall()
            stored = connection.execute(
                "SELECT biz_date,industry,activity_type,visitor_uv,order_count,"
                "pay_amount_cents FROM commerce_daily"
            ).fetchall()
            if sorted(derived) != sorted(stored):
                raise CommerceDataError("日报与上游多表重新计算结果不一致")
    except sqlite3.Error as exc:
        raise CommerceDataError("上游数据校验失败") from exc


def query_metric(
    metric: MetricDefinition, start: date, end: date, database_path: Path,
    group_by: str | None = None, *, candidate: MetricQuery | None = None,
    deadline: float | None = None,
) -> MetricResult:
    """Execute the exact validated SQL through one read-only boundary."""

    if metric not in load_metric_catalog(CATALOG_PATH):
        raise CommerceDataError("指标未在合成电商目录中批准")
    if start > end or end - start > timedelta(days=30):
        raise CommerceDataError("一次查询最多覆盖 31 天")
    try:
        connection, columns, first, last = _checked_connection(database_path)
        with closing(connection):
            if deadline is not None:
                connection.set_progress_handler(lambda: int(monotonic() >= deadline), 1000)
            if start.isoformat() < first or end.isoformat() > last:
                raise CommerceDataError(f"合成数据只覆盖 {first} 至 {last}")
            proposed = candidate or build_metric_query(metric, start, end, group_by)
            approved = validate_metric_query(
                proposed, metric, start, end, group_by, {"commerce_daily": columns & _COLUMNS}
            )
            rows = connection.execute(approved.statement, approved.parameters).fetchall()
            if group_by is None:
                if len(rows) != 1 or type(rows[0][0]) is not int:
                    raise CommerceDataError("汇总查询结果无效")
                return MetricResult(metric.name, start, end, None, rows[0][0], (), metric.calculation.unit)
            if len(rows) > 1000 or any(type(group) is not str or type(value) is not int for group, value in rows):
                raise CommerceDataError("分组结果无效或超过上限")
            groups = tuple((group, value) for group, value in rows)
            return MetricResult(metric.name, start, end, group_by, sum(v for _, v in groups), groups,
                                metric.calculation.unit)
    except (OSError, sqlite3.Error, ValueError) as exc:
        if isinstance(exc, CommerceDataError):
            raise
        if isinstance(exc, sqlite3.OperationalError) and "interrupted" in str(exc).lower():
            raise CommerceDataError("数据库查询超过时限") from exc
        raise CommerceDataError("查询未通过校验或数据库执行失败") from exc


def main() -> None:
    parser = argparse.ArgumentParser(description="创建唯一的合成电商 SQLite 数据库")
    parser.add_argument("--database", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()
    try:
        result = seed_commerce_db(args.database)
    except (OSError, sqlite3.Error, ValueError) as exc:
        parser.exit(2, f"创建失败：{exc}\n")
    print(json.dumps(asdict(result), ensure_ascii=False))
