"""The Query expert: inspect metrics and query an explicitly enabled local source."""

from datetime import date
from pathlib import Path
from typing import Literal, Sequence

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import BaseTool
from pydantic import ValidationError

from taobao_agent.analysis_agent import ComparisonEvidence
from taobao_agent.daily_metric_tool import (
    DailyMetricInput,
    DailyMetricValue,
    MetricNotApprovedError,
    MetricNotFoundError,
    make_daily_metric_tool,
)
from taobao_agent.harness import ToolUnavailableError, guarded_metric_search
from taobao_agent.intent_agent import IntentResult
from taobao_agent.metric_search import MetricDefinition
from taobao_agent.model import ToolCallingModel
from taobao_agent.uci_online_retail import UCIDateOutOfRange, UCIQueryError


class QueryEvidence(ComparisonEvidence):
    """Two verified daily totals handed from Query to Analysis, not user-facing text."""

    source: Literal["uci"]
    unit: Literal["pence"]


class ToolProposalError(Exception):
    """The model did not propose exactly the approved tool and arguments."""


def _query_day(
    daily_tool: BaseTool,
    metric_name: str,
    business_date: date,
    query_model: ToolCallingModel | None = None,
    question: str = "",
) -> DailyMetricValue:
    """Check a model's proposal before invoking the bounded tool."""

    arguments = {"metric_name": metric_name, "business_date": business_date.isoformat()}
    if query_model is not None:
        response = query_model.bind_tools([daily_tool], tool_choice="auto").invoke([
            SystemMessage(content=(
                "你是取数专家。本次只需查询一个已指定的指标和日期。"
                "请调用 query_daily_metric 一次，不要自行编造金额或更改指标、日期。"
            )),
            HumanMessage(content=(
                f"用户问题：{question}\n本次应查询：{metric_name}，{business_date.isoformat()}。"
            )),
        ])
        if response.invalid_tool_calls or len(response.tool_calls) != 1:
            raise ToolProposalError("模型未提出唯一且有效的工具调用")
        proposal = response.tool_calls[0]
        if proposal.get("name") != daily_tool.name:
            raise ToolProposalError("模型选择了未批准的工具")
        try:
            parsed = DailyMetricInput.model_validate(proposal.get("args"))
        except ValidationError as exc:
            raise ToolProposalError("模型工具参数格式无效") from exc
        if parsed.metric_name != metric_name or parsed.business_date != business_date:
            raise ToolProposalError("模型工具参数与已核准的任务不一致")
        arguments = parsed.model_dump(mode="json")

    try:
        value = DailyMetricValue.model_validate(daily_tool.invoke(arguments))
    except ValidationError as exc:
        raise MetricNotApprovedError("工具返回值格式无效") from exc
    if value.metric_name != metric_name or value.business_date != business_date:
        raise MetricNotApprovedError("工具返回的指标或日期与请求不一致")
    return value


def _supports_uci_metric(metric: MetricDefinition) -> bool:
    return (
        metric.name == "交易明细金额"
        and metric.calculation is not None
        and metric.calculation.unit == "pence"
    )


def _fetch_days(
    metric: MetricDefinition,
    catalog: Sequence[MetricDefinition],
    database_path: Path,
    days: tuple[date, ...],
    query_model: ToolCallingModel | None,
    question: str,
) -> tuple[DailyMetricValue, ...]:
    daily_tool = make_daily_metric_tool(catalog, uci_db_path=database_path)
    return tuple(
        _query_day(daily_tool, metric.name, day, query_model, question) for day in days
    )


def fetch_comparison(
    intent: IntentResult,
    catalog: Sequence[MetricDefinition],
    uci_db_path: Path | None = None,
    query_model: ToolCallingModel | None = None,
    question: str = "",
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

    if uci_db_path is not None:
        if not _supports_uci_metric(metric):
            return "UCI 公开数据模式目前只支持交易明细金额比较。"
        try:
            previous_result, current_result = _fetch_days(
                metric, catalog, uci_db_path, (intent.prev, intent.curr),
                query_model, question,
            )
        except UCIDateOutOfRange as exc:
            return f"UCI Online Retail 数据日期仅覆盖 {exc.first_date} 至 {exc.last_date}；当前没有返回比较结果。"
        except (UCIQueryError, ToolUnavailableError, MetricNotFoundError, MetricNotApprovedError, ToolProposalError):
            return "UCI 公开数据比较查询未通过校验或执行失败；当前没有返回比较结果。"
    else:
        return "已找到指标定义，但当前没有启用可查询的数据源；无法比较两个日期的数值。"

    if (previous_result.source, previous_result.unit) != (
        current_result.source, current_result.unit
    ):
        return "两次查询的来源或单位不一致；当前没有返回比较结果。"
    return QueryEvidence(
        metric_name=metric.name, prev=intent.prev, curr=intent.curr,
        previous_minor=previous_result.amount_minor,
        current_minor=current_result.amount_minor,
        source=previous_result.source, unit=previous_result.unit,
    )


def answer_query(
    intent: IntentResult,
    catalog: Sequence[MetricDefinition],
    uci_db_path: Path | None = None,
    query_model: ToolCallingModel | None = None,
    question: str = "",
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

    if uci_db_path is not None and intent.intent_type == "data_query":
        if not _supports_uci_metric(metric):
            return "UCI 公开数据模式目前只支持交易明细金额。"
        if intent.curr is None:
            return "请提供一个明确的查询日期；当前没有执行公开数据查询。"
        if intent.prev is not None or intent.scope or intent.dimensions or intent.secondary_intents:
            return "UCI 公开数据模式目前只支持单日、无分组的交易明细金额查询；当前没有执行查询。"
        try:
            (result,) = _fetch_days(
                metric, catalog, uci_db_path, (intent.curr,), query_model, question,
            )
        except UCIDateOutOfRange as exc:
            return f"UCI Online Retail 数据日期仅覆盖 {exc.first_date} 至 {exc.last_date}；当前没有返回数值。"
        except (UCIQueryError, ToolUnavailableError, MetricNotFoundError, MetricNotApprovedError, ToolProposalError):
            return "UCI 公开数据查询未通过校验或执行失败；当前没有返回数值。"
        amount_pounds = f"{result.amount_minor // 100}.{result.amount_minor % 100:02d}"
        return (
            f"UCI Online Retail 公开数据：{intent.curr.isoformat()} 的交易明细金额为 "
            f"{amount_pounds} 英镑（排除取消、非正数量和非正单价；不是已支付 GMV）。"
        )

    return (
        f"已找到指标定义：{metric.name} → {metric.table}.{metric.field}"
        f"（来源：{metric.source}）。当前尚未接入数据查询，无法返回数值。"
    )
