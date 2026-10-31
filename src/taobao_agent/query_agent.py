"""One Query expert: validate a task, call approved tools, return complete evidence."""

import json
import re
from datetime import date
from pathlib import Path
from typing import Sequence

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from taobao_agent.commerce_data import (
    SOURCE_ID, CommerceDataError, inspect_metric, query_metric,
)
from taobao_agent.harness import ToolUnavailableError, guarded_metric_search
from taobao_agent.intent_agent import IntentResult
from taobao_agent.metric_search import MetricDefinition
from taobao_agent.metric_sql import MetricQuery, build_metric_query
from taobao_agent.model import ToolCallingModel


class Period(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    start: date
    end: date

    @model_validator(mode="after")
    def check_order(self) -> "Period":
        if self.start > self.end:
            raise ValueError("查询时间范围倒置")
        return self


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    metric_names: tuple[str, ...] = Field(min_length=1, max_length=3)
    periods: tuple[Period, ...] = Field(min_length=1, max_length=7)
    group_by: str | None = None

    @model_validator(mode="after")
    def check_task(self) -> "QueryRequest":
        if len(set(self.metric_names)) != len(self.metric_names):
            raise ValueError("指标重复")
        if self.group_by not in (None, "行业"):
            raise ValueError("分组维度未获批准")
        if list(self.periods) != sorted(set(self.periods), key=lambda p: (p.start, p.end)):
            raise ValueError("查询时间段须互不重复且递增")
        return self


class GroupValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    value: int = Field(ge=0)


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    metric_name: str
    start: date
    end: date
    group_by: str | None
    total: int = Field(ge=0)
    groups: tuple[GroupValue, ...] = ()
    unit: str
    source: str

    @model_validator(mode="after")
    def check_groups(self) -> "Observation":
        if self.source != SOURCE_ID or self.start > self.end:
            raise ValueError("证据来源或日期无效")
        if self.group_by is None and self.groups:
            raise ValueError("无分组任务不能返回分组")
        if self.group_by is not None and sum(item.value for item in self.groups) != self.total:
            raise ValueError("分组合计与总值不一致")
        if len({item.name for item in self.groups}) != len(self.groups):
            raise ValueError("分组名称重复")
        return self


class QueryEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    request: QueryRequest
    results: tuple[Observation, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def check_complete(self) -> "QueryEvidence":
        expected = {
            (metric, period.start, period.end, self.request.group_by)
            for metric in self.request.metric_names for period in self.request.periods
        }
        actual = [(item.metric_name, item.start, item.end, item.group_by) for item in self.results]
        if len(actual) != len(expected) or set(actual) != expected:
            raise ValueError("查询证据缺失、重复或超出任务")
        return self


class MetricNamesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric_names: tuple[str, ...]


class QueryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric_names: tuple[str, ...]
    periods: tuple[Period, ...]
    group_by: str | None = None


class ToolProposalError(ValueError):
    """The LLM proposed a tool call outside the approved query task."""


def request_from_intent(intent: IntentResult, question: str) -> QueryRequest:
    """Turn validated Intent fields into a bounded, explicit query task."""

    if not intent.metric_name:
        raise ValueError("请提供要查询的指标名称")
    if intent.scope not in (None, "百亿补贴") or len(intent.dimensions) > 1:
        raise ValueError("当前只支持百亿补贴范围和一个分组维度")
    names = list(intent.metric_names or [intent.metric_name])
    if intent.intent_type == "attribution" and intent.metric_name == "GMV":
        names.extend(name for name in ("访客数", "订单数") if name not in names)
    if intent.date_range:
        if intent.intent_type == "attribution":
            raise ValueError("变化分析请给出至少两个明确日期")
        periods = (Period(start=intent.date_range.start, end=intent.date_range.end),)
    else:
        dates = intent.requested_dates()
        if not dates:
            raise ValueError("请提供明确的查询日期")
        periods = tuple(Period(start=day, end=day) for day in dates)
    explicit = set(re.findall(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)", question))
    covered = {
        day.isoformat() for period in periods for day in (period.start, period.end)
    }
    if not explicit.issubset(covered):
        raise ValueError("识别出的日期与问题中明确日期不一致")
    if intent.intent_type == "attribution" and len(periods) < 2:
        raise ValueError("变化分析请给出至少两个明确日期")
    return QueryRequest(metric_names=tuple(names), periods=periods,
                        group_by=intent.dimensions[0] if intent.dimensions else None)


def _run_tools(
    request: QueryRequest, catalog: Sequence[MetricDefinition],
    database_path: Path, model: ToolCallingModel, question: str,
) -> QueryEvidence:
    """One bounded ReAct loop; every executable query crosses the same SQL gate."""

    selected: tuple[MetricDefinition, ...] = ()
    rules_checked = False
    prepared: dict[tuple[str, date, date], MetricQuery] = {}

    @tool("search_metrics", args_schema=MetricNamesInput)
    def search_metrics(metric_names: tuple[str, ...]) -> list[dict[str, object]]:
        """Find approved metrics and their source table and columns."""
        found = tuple(guarded_metric_search(name, catalog) for name in metric_names)
        if any(metric is None for metric in found):
            raise CommerceDataError("指标不在已批准目录中")
        return [metric.model_dump(mode="json") for metric in found]

    @tool("rule_search", args_schema=MetricNamesInput)
    def rule_search(metric_names: tuple[str, ...]) -> list[dict[str, object]]:
        """Read approved filters, date fields, aggregation and units."""
        if not selected or metric_names != request.metric_names:
            raise ToolProposalError("尚未核准指标")
        return [metric.calculation.model_dump(mode="json") for metric in selected]

    @tool("build_sql", args_schema=QueryInput)
    def build_sql(metric_names: tuple[str, ...], periods: tuple[Period, ...],
                  group_by: str | None = None) -> list[dict[str, object]]:
        """Build parameterized SQL for the exact approved task."""
        if not rules_checked or (metric_names, periods, group_by) != (
            request.metric_names, request.periods, request.group_by
        ):
            raise ToolProposalError("构建 SQL 的任务与已核准请求不一致")
        for metric in selected:
            for period in periods:
                prepared[(metric.name, period.start, period.end)] = build_metric_query(
                    metric, period.start, period.end, group_by
                )
        return [
            {"metric_name": key[0], "start": key[1].isoformat(), "end": key[2].isoformat(),
             "statement": candidate.statement, "parameters": candidate.parameters}
            for key, candidate in prepared.items()
        ]

    @tool("execute_query", args_schema=QueryInput)
    def execute_query(metric_names: tuple[str, ...], periods: tuple[Period, ...],
                      group_by: str | None = None) -> list[dict[str, object]]:
        """Read the local database only after the SQL validator accepts every query."""
        if not prepared or (metric_names, periods, group_by) != (
            request.metric_names, request.periods, request.group_by
        ):
            raise ToolProposalError("执行任务与已构建 SQL 不一致")
        results = [
            query_metric(metric, period.start, period.end, database_path, group_by,
                         candidate=prepared[(metric.name, period.start, period.end)])
            for metric in selected for period in periods
        ]
        return [
            {"metric_name": item.metric_name, "start": item.start.isoformat(),
             "end": item.end.isoformat(), "group_by": item.group_by,
             "total": item.total, "groups": [{"name": name, "value": value} for name, value in item.groups],
             "unit": item.unit, "source": item.source}
            for item in results
        ]

    tools: dict[str, BaseTool] = {item.name: item for item in (
        search_metrics, rule_search, build_sql, execute_query
    )}
    bound = model.bind_tools(list(tools.values()), tool_choice="auto")
    messages = [
        SystemMessage(content=(
            "你是取数专家。只按已批准请求依次调用 search_metrics、rule_search、build_sql、"
            "execute_query，每轮只调用一个工具；执行后停止。不能改指标、日期、维度、SQL 或数据库路径。"
        )),
        HumanMessage(content=(
            f"用户问题：{question}\n已批准请求："
            f"{json.dumps(request.model_dump(mode='json'), ensure_ascii=False)}"
        )),
    ]
    expected = ("search_metrics", "rule_search", "build_sql", "execute_query")
    for index, name in enumerate(expected):
        expected_args: dict[str, object] = {"metric_names": list(request.metric_names)}
        if index >= 2:
            expected_args.update(
                periods=[period.model_dump(mode="json") for period in request.periods],
                group_by=request.group_by,
            )
        for attempt in range(3):
            response = bound.invoke(messages)
            if not isinstance(response, AIMessage):
                raise ToolProposalError(f"{name} 阶段模型没有返回可识别的工具消息")
            if not response.invalid_tool_calls and len(response.tool_calls) == 1:
                break
            if attempt == 2:
                raise ToolProposalError(f"{name} 阶段连续三次没有提出有效的单个工具调用")
            messages.append(HumanMessage(content=(
                f"刚才的 {name} 工具调用格式无效。下一步只调用 {name}，"
                f"参数严格使用这个 JSON 对象：{json.dumps(expected_args, ensure_ascii=False)}。"
                "不要输出普通文本，不要增加字段。"
            )))
        proposal = response.tool_calls[0]
        if proposal.get("name") != name or not proposal.get("id"):
            actual = proposal.get("name") if proposal.get("name") in tools else "未知工具"
            raise ToolProposalError(f"预期调用 {name}，实际调用 {actual} 或编号无效")
        messages.append(response)
        try:
            if name in ("search_metrics", "rule_search"):
                args = MetricNamesInput.model_validate(proposal.get("args"))
                if args.metric_names != request.metric_names:
                    raise ToolProposalError("工具指标与任务不一致")
                output = tools[name].invoke(args.model_dump(mode="json"))
                if name == "search_metrics":
                    selected = tuple(MetricDefinition.model_validate(item) for item in output)
                    if tuple(metric.name for metric in selected) != request.metric_names:
                        raise ToolProposalError("指标查找结果与任务不一致")
                else:
                    if output != [metric.calculation.model_dump(mode="json") for metric in selected]:
                        raise ToolProposalError("规则查找结果与指标不一致")
                    rules_checked = True
            else:
                args = QueryInput.model_validate(proposal.get("args"))
                if (args.metric_names, args.periods, args.group_by) != (
                    request.metric_names, request.periods, request.group_by
                ):
                    raise ToolProposalError("工具查询范围与任务不一致")
                output = tools[name].invoke(args.model_dump(mode="json"))
            messages.append(ToolMessage(content=json.dumps(output, ensure_ascii=False),
                                        tool_call_id=proposal["id"]))
        except ValidationError as exc:
            raise ToolProposalError("工具参数或结果格式无效") from exc
        if index == 3:
            results = tuple(Observation.model_validate(item) for item in output)
            return QueryEvidence(request=request, results=results)
    raise ToolProposalError("取数未完成")


def fetch_query_evidence(
    intent: IntentResult, question: str, catalog: Sequence[MetricDefinition],
    database_path: Path, model: ToolCallingModel,
) -> QueryEvidence | str:
    """Return complete verified evidence, or a constrained explanation without partial data."""

    try:
        request = request_from_intent(intent, question)
        return _run_tools(request, catalog, database_path, model, question)
    except (ValueError, CommerceDataError, ToolUnavailableError) as exc:
        if isinstance(exc, ToolProposalError):
            return f"取数专家的工具调用未通过顺序或参数校验（{exc}）；没有返回数据。"
        if isinstance(exc, CommerceDataError):
            return f"查询未完成：{exc}；没有返回数据。"
        return f"查询未完成：{exc}；没有返回数据。"


def _display(value: int, unit: str) -> str:
    if unit == "cents":
        return f"{value // 100}.{value % 100:02d} 元"
    return f"{value} {unit}"


def answer_query(evidence: QueryEvidence, *, include_supporting: bool = True) -> str:
    """Format values from Query evidence; no number is supplied by the model."""

    parts = []
    for item in evidence.results:
        if not include_supporting and item.metric_name != evidence.request.metric_names[0]:
            continue
        period = item.start.isoformat() if item.start == item.end else f"{item.start} 至 {item.end}"
        detail = f"{period} 的{item.metric_name}为 {_display(item.total, item.unit)}"
        if item.group_by:
            ranked = sorted(item.groups, key=lambda group: (-group.value, group.name))
            detail += "；按行业分组：" + "、".join(
                f"{group.name} {_display(group.value, item.unit)}" for group in ranked
            )
        parts.append(detail)
    return "教学合成数据（百亿补贴）：" + "；".join(parts) + "。"


def answer_table_meta(intent: IntentResult, catalog: Sequence[MetricDefinition],
                      database_path: Path) -> str:
    """Describe only an approved metric and verified physical columns."""

    if not intent.metric_name:
        return "请指定要查看口径的指标。"
    metric = guarded_metric_search(intent.metric_name, catalog)
    if metric is None:
        return f"未配置 {intent.metric_name} 的指标定义。"
    try:
        first, last, fields = inspect_metric(metric, database_path)
    except CommerceDataError:
        return "指标目录与数据库结构未通过核对。"
    rule = metric.calculation
    return (
        f"教学合成数据：{metric.name} 对应 {metric.table}.{metric.field}，"
        f"按 {rule.date_field} 汇总，只计 {rule.condition.field}={rule.condition.value}，"
        f"聚合方式 {rule.aggregation}，单位 {rule.unit}；"
        f"数据库已核对字段 {', '.join(fields)}，日期覆盖 {first} 至 {last}。"
    )
