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
