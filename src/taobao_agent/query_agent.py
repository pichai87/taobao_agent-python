"""The Query expert: find an approved metric and collect dated observations."""

import json
import re
from datetime import date
from pathlib import Path
from typing import Sequence

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from taobao_agent.analysis_agent import MetricSeriesEvidence
from taobao_agent.daily_metric_tool import (
    DailyMetricInput,
    DailyMetricValue,
    MetricNotApprovedError,
    MetricNotFoundError,
    make_daily_metric_tool,
)
from taobao_agent.harness import ToolUnavailableError, guarded_metric_search
from taobao_agent.intent_agent import IntentResult
from taobao_agent.metric_search import MetricCalculation, MetricDefinition, NonEmptyText
from taobao_agent.metric_sql import MetricQuery, build_metric_query
from taobao_agent.model import ToolCallingModel
from taobao_agent.uci_online_retail import UCIDateOutOfRange, UCIQueryError, inspect_uci_metric
from taobao_agent.uci_analytics import inspect_enriched_metric, is_enriched_catalog


class MetricQueryPlan(BaseModel):
    """The bounded task passed from Intent to the Query expert."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    metric_name: NonEmptyText
    dates: tuple[date, ...] = Field(min_length=1, max_length=7)

    @model_validator(mode="after")
    def check_dates(self) -> "MetricQueryPlan":
        if list(self.dates) != sorted(set(self.dates)):
            raise ValueError("dates must be distinct and increasing")
        return self


class MetricSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric_name: NonEmptyText


class BuildSqlInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric_name: NonEmptyText
    dates: tuple[date, ...] = Field(min_length=1, max_length=7)


class ToolProposalError(Exception):
    """The model proposed a tool call outside the approved task."""


def _validate_value(raw: object, plan: MetricQueryPlan, business_date: date) -> DailyMetricValue:
    try:
        value = DailyMetricValue.model_validate(raw)
    except ValidationError as exc:
        raise MetricNotApprovedError("工具返回值格式无效") from exc
    if value.metric_name != plan.metric_name or value.business_date != business_date:
        raise MetricNotApprovedError("工具返回的指标或日期与任务不一致")
    return value


def _run_tool_loop(
    plan: MetricQueryPlan,
    catalog: Sequence[MetricDefinition],
    database_path: Path,
    model: ToolCallingModel,
    question: str,
) -> tuple[DailyMetricValue, ...]:
    """Execute the approved metric, rule, SQL and read-only query chain."""

    @tool("search_metrics", args_schema=MetricSearchInput)
    def search_metric(metric_name: str) -> dict[str, object]:
        """Find the approved table, field and calculation rule for a metric."""

        found = guarded_metric_search(metric_name, catalog)
        if found is None:
            raise MetricNotFoundError("指标目录中没有该指标")
        return found.model_dump(mode="json")

    selected_metric: MetricDefinition | None = None
    approved_rule: MetricCalculation | None = None
    prepared_queries: dict[date, MetricQuery] = {}

    @tool("rule_search", args_schema=MetricSearchInput)
    def rule_search(metric_name: str) -> dict[str, object]:
        """Read the approved calculation rule for the selected metric."""

        if selected_metric is None or selected_metric.name != metric_name or selected_metric.calculation is None:
            raise MetricNotApprovedError("指标没有已批准的计算规则")
        return selected_metric.calculation.model_dump(mode="json")

    @tool("build_sql", args_schema=BuildSqlInput)
    def build_sql(metric_name: str, dates: tuple[date, ...]) -> dict[str, object]:
        """Build parameterized SQL for each approved date; do not accept raw SQL."""

        if selected_metric is None or selected_metric.name != metric_name:
            raise MetricNotApprovedError("指标尚未核准")
        candidates = {day: build_metric_query(selected_metric, day) for day in dates}
        prepared_queries.update(candidates)
        return {"queries": [
            {"business_date": day.isoformat(), "statement": query.statement,
             "parameters": list(query.parameters)}
            for day, query in candidates.items()
        ]}

    daily_tool = make_daily_metric_tool(
        catalog, uci_db_path=database_path, prepared_queries=prepared_queries,
    )
    tools: dict[str, BaseTool] = {
        item.name: item for item in (search_metric, rule_search, build_sql, daily_tool)
    }
    bound = model.bind_tools(list(tools.values()), tool_choice="auto")
    messages = [
        SystemMessage(content=(
            "你是取数专家。依次调用 search_metrics、rule_search、build_sql，"
            "最后用 query_daily_metric 查询任务中的每个日期。build_sql 一次传入全部核准日期。"
            "只调用提供的工具，不自行编造数据、表名或 SQL；完成指定日期后停止。"
        )),
        HumanMessage(content=(
            f"用户问题：{question}\n已核准任务：指标 {plan.metric_name}；"
            f"日期 {', '.join(day.isoformat() for day in plan.dates)}。"
        )),
    ]
    searched = False
    rule_found = False
    sql_built = False
    observations: dict[date, DailyMetricValue] = {}
    call_count = 0
    for _ in range(12):
        response = bound.invoke(messages)
        if not isinstance(response, AIMessage) or response.invalid_tool_calls or not response.tool_calls:
            raise ToolProposalError("模型未提出有效工具调用")
        messages.append(response)
        for proposal in response.tool_calls:
            call_count += 1
            if call_count > 12:
                raise ToolProposalError("工具调用超过上限")
            name, raw_args, call_id = proposal.get("name"), proposal.get("args"), proposal.get("id")
            if name not in tools or not isinstance(call_id, str) or not call_id:
                raise ToolProposalError("工具调用名称或编号无效")
            try:
                if name == "search_metrics":
                    args = MetricSearchInput.model_validate(raw_args)
                    if args.metric_name != plan.metric_name or searched:
                        raise ToolProposalError("指标查找与任务不一致或重复")
                    result = search_metric.invoke(args.model_dump(mode="json"))
                    metric = MetricDefinition.model_validate(result)
                    if metric.name != plan.metric_name:
                        raise ToolProposalError("指标查找结果与任务不一致")
                    if metric.name != "交易明细金额" or metric.calculation is None or metric.calculation.unit != "pence":
                        raise MetricNotApprovedError("当前数据源不支持该指标")
                    selected_metric = metric
                    searched = True
                    tool_result = metric.model_dump(mode="json")
                elif name == "rule_search":
                    args = MetricSearchInput.model_validate(raw_args)
                    if not searched or rule_found or args.metric_name != plan.metric_name:
                        raise ToolProposalError("计算规则查找顺序或指标不正确")
                    rule = MetricCalculation.model_validate(rule_search.invoke(args.model_dump(mode="json")))
                    if selected_metric is None or rule != selected_metric.calculation:
                        raise ToolProposalError("计算规则与指标定义不一致")
                    approved_rule = rule
                    rule_found = True
                    tool_result = rule.model_dump(mode="json")
                elif name == "build_sql":
                    args = BuildSqlInput.model_validate(raw_args)
                    if not rule_found or sql_built or args.metric_name != plan.metric_name or args.dates != plan.dates:
                        raise ToolProposalError("SQL 构建顺序、指标或日期与任务不一致")
                    if selected_metric is None or approved_rule != selected_metric.calculation:
                        raise ToolProposalError("SQL 构建缺少已核准规则")
                    tool_result = build_sql.invoke(args.model_dump(mode="json"))
                    if set(prepared_queries) != set(plan.dates):
                        raise ToolProposalError("SQL 构建结果缺少日期")
                    sql_built = True
                else:
                    args = DailyMetricInput.model_validate(raw_args)
                    if not sql_built or args.metric_name != plan.metric_name:
                        raise ToolProposalError("查询前未完成指标、规则和 SQL 核准")
                    if args.business_date not in plan.dates or args.business_date in observations:
                        raise ToolProposalError("查询日期与任务不一致或重复")
                    raw = daily_tool.invoke(args.model_dump(mode="json"))
                    value = _validate_value(raw, plan, args.business_date)
                    observations[args.business_date] = value
                    tool_result = value.model_dump(mode="json")
            except ValidationError as exc:
                raise ToolProposalError("工具参数或查找结果格式无效") from exc
            messages.append(ToolMessage(
                content=json.dumps(tool_result, ensure_ascii=False), tool_call_id=call_id,
            ))
        if len(observations) == len(plan.dates):
            return tuple(observations[day] for day in plan.dates)
    raise ToolProposalError("取数任务未在执行上限内完成")


def fetch_metric_series(
    intent: IntentResult,
    catalog: Sequence[MetricDefinition],
    uci_db_path: Path | None = None,
    query_model: ToolCallingModel | None = None,
    question: str = "",
) -> MetricSeriesEvidence | str:
    """Return all requested observations together, or no partial evidence."""

    if not intent.metric_name:
        return "请提供要查询的指标名称；当前没有执行取数。"
    dates = intent.requested_dates()
    if not dates:
        return "请提供明确的查询日期；当前没有执行取数。"
    explicit_dates = set(re.findall(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)", question))
    if not explicit_dates.issubset({day.isoformat() for day in dates}):
        return "意图识别的日期与问题中明确写出的日期不一致；当前没有执行取数。"
    if intent.scope or intent.dimensions or intent.secondary_intents:
        return "当前数据源暂不支持指定范围、分组或复合任务；当前没有执行取数。"
    try:
        plan = MetricQueryPlan(metric_name=intent.metric_name, dates=dates)
    except ValidationError:
        return "请提供不超过 7 个、互不重复且按时间递增的日期；当前没有执行取数。"
    if intent.intent_type == "attribution" and len(plan.dates) < 2:
        return "请提供至少两个不同的明确日期；当前没有执行分析。"

    if uci_db_path is None:
        try:
            metric = guarded_metric_search(plan.metric_name, catalog)
        except ToolUnavailableError:
            return "指标目录暂时不可用；当前没有执行取数。"
        if metric is None:
            return f"尚未配置 {plan.metric_name} 的指标定义；当前没有执行取数。"
        return "已找到指标定义，但当前没有启用可查询的数据源；无法返回数值。"

    try:
        if query_model is None:
            metric = guarded_metric_search(plan.metric_name, catalog)
            if metric is None:
                return f"尚未配置 {plan.metric_name} 的指标定义；当前没有执行取数。"
            if metric.name != "交易明细金额" or metric.calculation is None or metric.calculation.unit != "pence":
                return "UCI 公开数据模式目前只支持交易明细金额。"
            daily_tool = make_daily_metric_tool(catalog, uci_db_path=uci_db_path)
            values = tuple(
                _validate_value(daily_tool.invoke({
                    "metric_name": plan.metric_name, "business_date": day.isoformat(),
                }), plan, day)
                for day in plan.dates
            )
        else:
            values = _run_tool_loop(plan, catalog, uci_db_path, query_model, question)
        return MetricSeriesEvidence(metric_name=plan.metric_name, values=values)
    except UCIDateOutOfRange as exc:
        return f"UCI Online Retail 数据日期仅覆盖 {exc.first_date} 至 {exc.last_date}；当前没有返回数值。"
    except MetricNotFoundError:
        return f"尚未配置 {plan.metric_name} 的指标定义；当前没有执行取数。"
    except MetricNotApprovedError:
        return "UCI 公开数据查询未通过指标审批或执行校验；当前没有返回数值。"
    except (UCIQueryError, ToolUnavailableError, ToolProposalError, ValidationError):
        return "UCI 公开数据查询未通过校验或执行失败；当前没有返回数值。"


def answer_query(
    intent: IntentResult,
    catalog: Sequence[MetricDefinition],
    uci_db_path: Path | None = None,
    query_model: ToolCallingModel | None = None,
    question: str = "",
) -> str:
    """Answer a dated query, or show available metadata when no source is enabled."""

    if uci_db_path is None:
        if not intent.metric_name:
            return "请提供要查询的指标名称。"
        try:
            metric = guarded_metric_search(intent.metric_name, catalog)
        except ToolUnavailableError:
            return "指标目录暂时不可用，请稍后重试。当前没有执行数据查询。"
        if metric is None:
            return f"尚未配置 {intent.metric_name} 的指标定义，无法查询数据。"
        return (
            f"已找到指标定义：{metric.name} → {metric.table}.{metric.field}"
            f"（来源：{metric.source}）。当前尚未接入数据查询，无法返回数值。"
        )

    result = fetch_metric_series(intent, catalog, uci_db_path, query_model, question)
    if isinstance(result, str):
        return result
    amounts = "，".join(
        f"{value.business_date.isoformat()} 的{value.metric_name}为 "
        f"{value.amount_minor // 100}.{value.amount_minor % 100:02d} 英镑"
        for value in result.values
    )
    return (
        f"UCI Online Retail 公开数据：{amounts}"
        "（排除取消、非正数量和非正单价；不是已支付 GMV）。"
    )


def answer_table_meta(
    intent: IntentResult, catalog: Sequence[MetricDefinition], uci_db_path: Path | None = None,
) -> str:
    """Explain an approved metric's rule and, when enabled, its actual source columns."""

    if not intent.metric_name:
        return "请说明要了解哪个指标的口径或表字段。"
    if intent.scope or intent.dimensions:
        return "当前表结构说明不支持指定范围或分组。"
    try:
        metric = guarded_metric_search(intent.metric_name, catalog)
    except ToolUnavailableError:
        return "指标目录暂时不可用，无法说明指标口径。"
    if metric is None:
        return f"尚未配置 {intent.metric_name} 的指标定义，无法说明表结构。"
    if uci_db_path is None:
        return (
            f"指标目录记录：{metric.name} 对应 {metric.table}.{metric.field}，"
            f"来源为 {metric.source}。当前未连接数据源，表字段和口径尚未核对。"
        )
    try:
        profile = (
            inspect_enriched_metric(metric, uci_db_path)
            if is_enriched_catalog(catalog) else inspect_uci_metric(metric, uci_db_path)
        )
    except UCIQueryError:
        return "UCI 公开数据的指标或表字段未通过核对，当前无法说明表结构。"
    calculation = metric.calculation
    if calculation is None:
        return "指标缺少已核准的计算规则。"
    return (
        f"UCI Online Retail 公开数据：{metric.name} 对应表 {metric.table}，"
        f"指标字段 {metric.field}；按 {calculation.date_field} 汇总，"
        f"只计入 {calculation.condition.field}={calculation.condition.value} 的行，"
        f"计算方式 {calculation.aggregation}，单位 {calculation.unit}。"
        f"数据库已核对字段：{', '.join(profile.approved_columns)}；"
        f"数据日期 {profile.first_date} 至 {profile.last_date}。"
        + (
            "这不是已支付 GMV；有国家和商品编码字段，没有行业、访客或支付字段。"
            if is_enriched_catalog(catalog)
            else "这不是已支付 GMV；当前没有商品、行业或访客字段。"
        )
    )
