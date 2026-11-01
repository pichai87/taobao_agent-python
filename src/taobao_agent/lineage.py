"""Trace one approved metric from its reviewed SQL to the local fixture."""

from datetime import date
from pathlib import Path

from sqlglot import exp, parse_one

from taobao_agent.commerce_data import FIXTURE_PATH, inspect_metric
from taobao_agent.knowledge import KnowledgeBase
from taobao_agent.metric_search import MetricDefinition
from taobao_agent.metric_sql import build_metric_query


def describe_lineage(metric: MetricDefinition, database_path: Path,
                     knowledge: KnowledgeBase) -> str:
    """Use an AST to verify the SQL references before describing source fields."""

    _, _, physical_fields = inspect_metric(metric, database_path)
    query = build_metric_query(metric, date(2026, 10, 20), date(2026, 10, 20))
    tree = parse_one(query.statement, read="sqlite")
    tables = {node.name for node in tree.find_all(exp.Table)}
    fields = {node.name for node in tree.find_all(exp.Column)}
    expected = {metric.field, metric.calculation.date_field,
                metric.calculation.condition.field}
    if tables != {knowledge.model.table} or fields != expected or not expected <= set(physical_fields):
        raise ValueError("血缘 SQL 与已批准指标或数据库字段不一致")
    return (
        f"{metric.name} 血缘：原始文件 {FIXTURE_PATH.name} → "
        f"{knowledge.model.table}.{metric.field} → {metric.calculation.aggregation.upper()} "
        f"→ {metric.name}；日期字段 {metric.calculation.date_field}，"
        f"筛选 {metric.calculation.condition.field}={metric.calculation.condition.value}。"
        "这是当前单表指标的字段血缘，不包含尚未接入的上游 ETL 过程。"
    )
