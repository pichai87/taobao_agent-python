"""Trace approved metrics through query SQL and the reviewed multi-table ETL."""

from datetime import date
from pathlib import Path

from sqlglot import exp, parse_one

from taobao_agent.commerce_data import FIXTURE_PATH, inspect_metric, verify_etl
from taobao_agent.knowledge import KnowledgeBase
from taobao_agent.metric_search import MetricDefinition
from taobao_agent.metric_sql import build_metric_query
from taobao_agent.tools.etl import DAILY_ETL_SQL

_UPSTREAM = {
    "pay_amount_cents": "SUM(fact_order.pay_amount_cents WHERE order_status=paid)",
    "order_count": "COUNT(fact_order.order_id WHERE order_status=paid)",
    "visitor_uv": "fact_visit_daily.visitor_uv",
}


def describe_lineage(metric: MetricDefinition, database_path: Path,
                     knowledge: KnowledgeBase) -> str:
    """Use an AST to verify the SQL references before describing source fields."""

    _, _, physical_fields = inspect_metric(metric, database_path)
    verify_etl(database_path)
    query = build_metric_query(metric, date(2026, 10, 20), date(2026, 10, 20))
    tree = parse_one(query.statement, read="sqlite")
    tables = {node.name for node in tree.find_all(exp.Table)}
    fields = {node.name for node in tree.find_all(exp.Column)}
    expected = {metric.field, metric.calculation.date_field,
                metric.calculation.condition.field}
    if tables != {knowledge.model.table} or fields != expected or not expected <= set(physical_fields):
        raise ValueError("血缘 SQL 与已批准指标或数据库字段不一致")
    etl = parse_one(DAILY_ETL_SQL, read="sqlite")
    etl_tables = {node.name for node in etl.find_all(exp.Table)}
    etl_fields = {node.name for node in etl.find_all(exp.Column)}
    required_tables = {"commerce_daily", "fact_visit_daily", "fact_order",
                       "dim_product", "dim_activity"}
    required_fields = {"biz_date", "industry", "activity_type", "visitor_uv",
                       "order_id", "order_status", "pay_amount_cents"}
    if (etl_tables != required_tables or not required_fields <= etl_fields
            or knowledge.model.upstream.get(metric.field) != _UPSTREAM.get(metric.field)
            or "order_status = 'paid'" not in DAILY_ETL_SQL):
        raise ValueError("上游 ETL 与已批准字段血缘不一致")
    return (
        f"{metric.name} 血缘：原始文件 {FIXTURE_PATH.name} → "
        f"上游 ETL（{knowledge.model.upstream[metric.field]}，"
        "关联 dim_product 与 dim_activity）→ "
        f"{knowledge.model.table}.{metric.field} → {metric.calculation.aggregation.upper()} "
        f"→ {metric.name}；日期字段 {metric.calculation.date_field}，"
        f"筛选 {metric.calculation.condition.field}={metric.calculation.condition.value}。"
        "原始订单由教学汇总样例确定性拆分而来，不是独立采集的真实订单。"
    )
