"""One bounded daily-metric tool for the Query expert's enabled local source."""

from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Literal, Sequence

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, ConfigDict, Field

from taobao_agent.harness import guarded_metric_search
from taobao_agent.metric_catalog import load_metric_catalog
from taobao_agent.metric_search import MetricDefinition, NonEmptyText
from taobao_agent.metric_sql import MetricQuery
from taobao_agent.uci_online_retail import UCI_CATALOG_PATH, query_uci_metric


class DailyMetricInput(BaseModel):
    """The only arguments a future model may supply to this tool."""

    model_config = ConfigDict(extra="forbid")

    metric_name: NonEmptyText
    business_date: date


class DailyMetricValue(BaseModel):
    """One sourced result; the amount remains in cents or pence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    metric_name: str
    business_date: date
    amount_minor: int = Field(ge=0)
    source: Literal["uci"]
    unit: Literal["pence"]


class MetricNotFoundError(Exception):
    """The requested metric has no approved catalog entry."""


class MetricNotApprovedError(Exception):
    """The catalog entry is not approved for the enabled local source."""


def make_daily_metric_tool(
    catalog: Sequence[MetricDefinition],
    *,
    uci_db_path: Path,
    prepared_queries: Mapping[date, MetricQuery] | None = None,
) -> BaseTool:
    """Bind the approved UCI source; expose only metric name and date."""

    @tool(
        "query_daily_metric",
        args_schema=DailyMetricInput,
        description="按已批准的指标定义与日期，返回配置数据源中的单日整数金额及单位。",
    )
    def query_daily_metric(metric_name: str, business_date: date) -> dict[str, object]:
        metric = guarded_metric_search(metric_name, catalog)
        if metric is None:
            raise MetricNotFoundError("指标目录中没有该指标")

        if metric != load_metric_catalog(UCI_CATALOG_PATH)[0]:
            raise MetricNotApprovedError("指标不在 UCI 数据批准范围内")
        if prepared_queries is None:
            amount = query_uci_metric(metric, business_date, uci_db_path)
        else:
            candidate = prepared_queries.get(business_date)
            if candidate is None:
                raise MetricNotApprovedError("查询日期尚未生成 SQL")
            amount = query_uci_metric(metric, business_date, uci_db_path, candidate=candidate)

        if amount < 0:
            raise MetricNotApprovedError("查询结果不是非负金额")
        return DailyMetricValue(
            metric_name=metric.name,
            business_date=business_date,
            amount_minor=amount,
            source="uci",
            unit="pence",
        ).model_dump(mode="json")

    return query_daily_metric
