"""The Query expert: inspect metrics and query an explicitly enabled local source."""

from datetime import date
from pathlib import Path
from typing import Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from taobao_agent.demo_query_tool import DemoQueryError, query_demo_metric
from taobao_agent.harness import ToolUnavailableError, guarded_metric_search
from taobao_agent.intent_agent import IntentResult
from taobao_agent.metric_search import MetricDefinition
from taobao_agent.uci_online_retail import UCIDateOutOfRange, UCIQueryError, query_uci_metric


class QueryEvidence(BaseModel):
    """Two verified daily totals handed from Query to Analysis, not user-facing text."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    metric_name: str
    prev: date
    curr: date
    previous_minor: int = Field(ge=0)
    current_minor: int = Field(ge=0)
    source: Literal["demo", "uci"]
    unit: Literal["fen", "pence"]

    @model_validator(mode="after")
    def check_source_unit(self) -> "QueryEvidence":
        if (self.source, self.unit) not in {("demo", "fen"), ("uci", "pence")}:
            raise ValueError("query evidence source and unit do not match")
        return self


def fetch_comparison(
    intent: IntentResult,
    catalog: Sequence[MetricDefinition],
    demo_data: bool = False,
    uci_db_path: Path | None = None,
) -> QueryEvidence | str:
    """Return two approved totals, or a safe explanation without partial numbers."""

    if not intent.metric_name:
        return "请提供要比较的指标名称；当前没有执行分析。"
    if intent.curr is None or intent.prev is None or intent.curr == intent.prev:
        return "请提供两个不同的明确日期；当前没有执行分析。"
    if intent.scope or intent.dimensions or intent.secondary_intents:
        return "当前只支持同一指标的两个日期总额比较，不支持指定范围、分组或复合任务；当前没有执行分析。"

    try:
        metric = guarded_metric_search(intent.metric_name, catalog)
    except ToolUnavailableError:
        return "指标目录暂时不可用；当前没有执行分析。"
    if metric is None:
        return f"尚未配置 {intent.metric_name} 的指标定义；当前没有执行分析。"

    if demo_data:
        if metric.name != "GMV" or metric.calculation is None or metric.calculation.unit != "fen":
            return "教学数据模式目前只支持已配置完整规则、单位为分的 GMV 比较。"
        try:
            previous = query_demo_metric(metric, intent.prev)
            current = query_demo_metric(metric, intent.curr)
        except DemoQueryError:
            return "教学数据比较查询未通过校验或执行失败；当前没有返回比较结果。"
        source: Literal["demo", "uci"] = "demo"
        unit: Literal["fen", "pence"] = "fen"
    elif uci_db_path is not None:
        if metric.name != "交易明细金额" or metric.calculation is None or metric.calculation.unit != "pence":
            return "UCI 公开数据模式目前只支持交易明细金额比较。"
        try:
            previous = query_uci_metric(metric, intent.prev, uci_db_path)
            current = query_uci_metric(metric, intent.curr, uci_db_path)
        except UCIDateOutOfRange as exc:
            return f"UCI Online Retail 数据日期仅覆盖 {exc.first_date} 至 {exc.last_date}；当前没有返回比较结果。"
        except UCIQueryError:
            return "UCI 公开数据比较查询未通过校验或执行失败；当前没有返回比较结果。"
        source = "uci"
        unit = "pence"
    else:
        return "已找到指标定义，但当前没有启用可查询的数据源；无法比较两个日期的数值。"

    if previous < 0 or current < 0:
        return "查询结果不是可比较的非负金额；当前没有返回比较结果。"
    return QueryEvidence(
        metric_name=metric.name, prev=intent.prev, curr=intent.curr,
        previous_minor=previous, current_minor=current, source=source, unit=unit,
    )


def answer_query(
    intent: IntentResult,
    catalog: Sequence[MetricDefinition],
    demo_data: bool = False,
    uci_db_path: Path | None = None,
) -> str:
    """Look up a metric; query only an explicitly enabled, supported data source."""

    if not intent.metric_name:
        return "请提供要查询的指标名称。"

    try:
        metric = guarded_metric_search(intent.metric_name, catalog)
    except ToolUnavailableError:
        return "指标目录暂时不可用，请稍后重试。当前没有执行数据查询。"
    if metric is None:
        return f"尚未配置 {intent.metric_name} 的指标定义，无法查询数据。"

    if demo_data and intent.intent_type == "data_query":
        if metric.name != "GMV" or metric.calculation is None:
            return "教学数据模式目前只支持已配置完整计算规则的 GMV。"
        if metric.calculation.unit != "fen":
            return "教学数据模式目前只支持单位为分的 GMV。"
        if intent.curr is None:
            return "请提供一个明确的查询日期；当前没有执行教学数据查询。"
        if intent.prev is not None or intent.scope or intent.dimensions or intent.secondary_intents:
            return "教学数据模式目前只支持单日、无分组的 GMV 查询；当前没有执行查询。"
        try:
            amount_fen = query_demo_metric(metric, intent.curr)
        except DemoQueryError:
            return "教学数据查询未通过校验或执行失败；当前没有返回数值。"
        amount_yuan = f"{amount_fen // 100}.{amount_fen % 100:02d}"
        return f"教学合成数据：{intent.curr.isoformat()} 的 GMV 为 {amount_yuan} 元。"

    if uci_db_path is not None and intent.intent_type == "data_query":
        if metric.name != "交易明细金额" or metric.calculation is None:
            return "UCI 公开数据模式目前只支持交易明细金额。"
        if metric.calculation.unit != "pence":
            return "UCI 公开数据模式目前只支持以便士为单位的指标。"
        if intent.curr is None:
            return "请提供一个明确的查询日期；当前没有执行公开数据查询。"
        if intent.prev is not None or intent.scope or intent.dimensions or intent.secondary_intents:
            return "UCI 公开数据模式目前只支持单日、无分组的交易明细金额查询；当前没有执行查询。"
        try:
            amount_pence = query_uci_metric(metric, intent.curr, uci_db_path)
        except UCIDateOutOfRange as exc:
            return f"UCI Online Retail 数据日期仅覆盖 {exc.first_date} 至 {exc.last_date}；当前没有返回数值。"
        except UCIQueryError:
            return "UCI 公开数据查询未通过校验或执行失败；当前没有返回数值。"
        amount_pounds = f"{amount_pence // 100}.{amount_pence % 100:02d}"
        return (
            f"UCI Online Retail 公开数据：{intent.curr.isoformat()} 的交易明细金额为 "
            f"{amount_pounds} 英镑（排除取消、非正数量和非正单价；不是已支付 GMV）。"
        )

    return (
        f"已找到指标定义：{metric.name} → {metric.table}.{metric.field}"
        f"（来源：{metric.source}）。当前尚未接入数据查询，无法返回数值。"
    )
