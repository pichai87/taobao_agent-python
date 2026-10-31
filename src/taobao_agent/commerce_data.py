"""Versioned synthetic commerce fixture and its single read-only SQLite executor."""

import argparse
import csv
import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path

from taobao_agent.metric_catalog import load_metric_catalog
from taobao_agent.metric_search import MetricDefinition
from taobao_agent.metric_sql import MetricQuery, build_metric_query, validate_metric_query


SOURCE_ID = "synthetic-commerce-v1"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = PROJECT_ROOT / "examples" / "commerce_daily.csv"
CATALOG_PATH = PROJECT_ROOT / "examples" / "commerce_metrics.json"
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "commerce" / "commerce.sqlite"
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
        with closing(sqlite3.connect(database_path)) as connection:
            connection.executescript(
                "CREATE TABLE commerce_daily ("
                "biz_date TEXT NOT NULL, industry TEXT NOT NULL, activity_type TEXT NOT NULL, "
                "visitor_uv INTEGER NOT NULL CHECK (visitor_uv >= 0), "
                "order_count INTEGER NOT NULL CHECK (order_count >= 0), "
                "pay_amount_cents INTEGER NOT NULL CHECK (pay_amount_cents >= 0), "
                "PRIMARY KEY (biz_date, industry, activity_type));"
                "CREATE TABLE import_metadata (source_id TEXT NOT NULL, fixture_sha256 TEXT NOT NULL, "
                "first_date TEXT NOT NULL, last_date TEXT NOT NULL);"
            )
            connection.executemany("INSERT INTO commerce_daily VALUES (?, ?, ?, ?, ?, ?)", rows)
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


def query_metric(
    metric: MetricDefinition, start: date, end: date, database_path: Path,
    group_by: str | None = None, *, candidate: MetricQuery | None = None,
) -> MetricResult:
    """Execute the exact validated SQL through one read-only boundary."""

    if metric not in load_metric_catalog(CATALOG_PATH):
        raise CommerceDataError("指标未在合成电商目录中批准")
    if start > end or end - start > timedelta(days=30):
        raise CommerceDataError("一次查询最多覆盖 31 天")
    try:
        connection, columns, first, last = _checked_connection(database_path)
        with closing(connection):
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
