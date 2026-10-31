"""Build and gate the one metric query shape supported by the demo."""

import re
from collections.abc import Mapping, Set
from dataclasses import dataclass
from datetime import date

from taobao_agent.metric_search import MetricDefinition


_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


@dataclass(frozen=True)
class MetricQuery:
    statement: str
    parameters: tuple[str, str]


@dataclass(frozen=True)
class AggregateQuery:
    """A bounded range query with an optional approved group column."""

    statement: str
    parameters: tuple[str, str, str]


_GROUP_COLUMNS = {"国家": "country", "商品编码": "stock_code"}


def build_aggregate_query(
    metric: MetricDefinition, start: date, end: date, group_by: str | None = None,
) -> AggregateQuery:
    """Build the only range/group SQL shape supported by the enriched UCI source."""

    calculation = metric.calculation
    if calculation is None or type(start) is not date or type(end) is not date or start > end:
        raise ValueError("指标规则或日期范围无效")
    if group_by is not None and group_by not in _GROUP_COLUMNS:
        raise ValueError("分组维度未获批准")
    table = _quoted_identifier(metric.table)
    amount = _quoted_identifier(metric.field)
    date_field = _quoted_identifier(calculation.date_field)
    condition_field = _quoted_identifier(calculation.condition.field)
    group_column = _quoted_identifier(_GROUP_COLUMNS[group_by]) if group_by else None
    select = f"{group_column} AS group_value, " if group_column else ""
    suffix = f" GROUP BY {group_column} ORDER BY group_value" if group_column else ""
    statement = (
        f"SELECT {select}COALESCE(SUM({amount}), 0) AS value FROM {table} "
        f"WHERE {date_field} >= ? AND {date_field} <= ? AND {condition_field} = ?{suffix}"
    )
    return AggregateQuery(statement, (start.isoformat(), end.isoformat(), calculation.condition.value))


def validate_aggregate_query(
    query: AggregateQuery, metric: MetricDefinition, start: date, end: date,
    group_by: str | None, approved_columns: Mapping[str, Set[str]],
) -> AggregateQuery:
    """Reject changed SQL, parameters, tables or columns immediately before execution."""

    expected = build_aggregate_query(metric, start, end, group_by)
    calculation = metric.calculation
    if calculation is None:
        raise ValueError("指标缺少计算规则")
    required = {metric.field, calculation.date_field, calculation.condition.field}
    if group_by is not None:
        required.add(_GROUP_COLUMNS[group_by])
    available = approved_columns.get(metric.table)
    if available is None or not required <= available or query != expected:
        raise ValueError("聚合 SQL、参数或字段未获批准")
    return query


def _quoted_identifier(value: str) -> str:
    if _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"不支持的表或字段标识符：{value}")
    return f'"{value}"'


def build_metric_query(metric: MetricDefinition, business_date: date) -> MetricQuery:
    """Create a parameterized SELECT from a structured sum rule."""

    calculation = metric.calculation
    if calculation is None:
        raise ValueError(f"指标缺少计算规则：{metric.name}")
    if type(business_date) is not date:
        raise ValueError("查询日期必须是 date 对象")

    table = _quoted_identifier(metric.table)
    amount = _quoted_identifier(metric.field)
    date_field = _quoted_identifier(calculation.date_field)
    condition_field = _quoted_identifier(calculation.condition.field)
    statement = (
        f"SELECT COALESCE(SUM({amount}), 0) AS value FROM {table} "
        f"WHERE {date_field} = ? AND {condition_field} = ?"
    )
    return MetricQuery(
        statement=statement,
        parameters=(business_date.isoformat(), calculation.condition.value),
    )


def validate_metric_query(
    query: MetricQuery,
    metric: MetricDefinition,
    business_date: date,
    approved_columns: Mapping[str, Set[str]],
) -> MetricQuery:
    """Allow only the exact query and approved fields for this metric and date."""

    expected = build_metric_query(metric, business_date)
    calculation = metric.calculation
    if calculation is None:
        raise ValueError(f"指标缺少计算规则：{metric.name}")
    required = {metric.field, calculation.date_field, calculation.condition.field}
    available = approved_columns.get(metric.table)
    if available is None or not required <= available:
        raise ValueError("指标引用的表或字段未获批准")
    if query != expected:
        raise ValueError("SQL 或参数与已批准的指标规则不一致")
    return query
