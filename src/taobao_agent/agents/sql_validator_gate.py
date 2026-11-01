"""Post-generation SQL gate at the database execution boundary."""

from collections.abc import Mapping, Set
from datetime import date

from taobao_agent.metric_search import MetricDefinition
from taobao_agent.metric_sql import MetricQuery, _GROUP_COLUMNS, build_metric_query


def validate_metric_query(
    candidate: MetricQuery, metric: MetricDefinition, start: date, end: date,
    group_by: str | None, approved_columns: Mapping[str, Set[str]],
) -> MetricQuery:
    """Compare full SQL, bound parameters and actual approved columns."""

    expected = build_metric_query(metric, start, end, group_by)
    calculation = metric.calculation
    required = {metric.field, calculation.date_field, calculation.condition.field}
    if group_by:
        required.add(_GROUP_COLUMNS[group_by])
    actual = approved_columns.get(metric.table)
    if actual is None or not required <= actual or candidate != expected:
        raise ValueError("SQL、参数或字段未获批准")
    return candidate
