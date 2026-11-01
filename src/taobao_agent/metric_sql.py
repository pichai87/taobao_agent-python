"""One approved SQL shape for every commerce metric, period and optional group."""

import re
from dataclasses import dataclass
from datetime import date

from taobao_agent.metric_search import MetricDefinition


_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_GROUP_COLUMNS = {"行业": "industry"}


@dataclass(frozen=True)
class MetricQuery:
    statement: str
    parameters: tuple[str, str, str]


def _identifier(value: str) -> str:
    if _IDENTIFIER.fullmatch(value) is None:
        raise ValueError("表名或字段名不符合批准的标识符格式")
    return f'"{value}"'


def build_metric_query(
    metric: MetricDefinition, start: date, end: date, group_by: str | None = None,
) -> MetricQuery:
    """Build a parameterized SUM over one approved period."""

    calculation = metric.calculation
    if calculation is None or type(start) is not date or type(end) is not date or start > end:
        raise ValueError("指标规则或日期范围无效")
    if group_by is not None and group_by not in _GROUP_COLUMNS:
        raise ValueError("分组维度未获批准")
    table = _identifier(metric.table)
    value = _identifier(metric.field)
    day = _identifier(calculation.date_field)
    condition = _identifier(calculation.condition.field)
    group = _identifier(_GROUP_COLUMNS[group_by]) if group_by else None
    select_group = f"{group} AS group_value, " if group else ""
    group_clause = f" GROUP BY {group} ORDER BY group_value" if group else ""
    statement = (
        f"SELECT {select_group}COALESCE(SUM({value}), 0) AS value FROM {table} "
        f"WHERE {day} >= ? AND {day} <= ? AND {condition} = ?{group_clause}"
    )
    return MetricQuery(statement, (start.isoformat(), end.isoformat(), calculation.condition.value))
