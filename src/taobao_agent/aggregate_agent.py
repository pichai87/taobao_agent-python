"""Bounded Query and Analysis for enriched public-data metrics."""

import re
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from taobao_agent.intent_agent import IntentResult
from taobao_agent.metric_search import MetricDefinition, search_metrics
from taobao_agent.metric_sql import build_aggregate_query
from taobao_agent.uci_analytics import AggregateResult, is_enriched_catalog, query_uci_aggregate
from taobao_agent.uci_online_retail import UCIDateOutOfRange, UCIQueryError


class AggregateEvidence(BaseModel):
    """All verified results for one approved user task; no partial handoff."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    results: tuple[AggregateResult, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def check_source(self) -> "AggregateEvidence":
        if {item.source for item in self.results} != {"uci"}:
            raise ValueError("聚合证据来源不一致")
        keys = [(item.metric_name, item.start, item.end, item.group_by) for item in self.results]
        if len(keys) != len(set(keys)):
            raise ValueError("聚合证据重复")
        return self


def _display(value: int, unit: str) -> str:
    if unit == "pence":
        return f"{value // 100}.{value % 100:02d} 英镑"
    return f"{value} {unit}"


def fetch_aggregate_evidence(
    intent: IntentResult, question: str, catalog: Sequence[MetricDefinition], database_path: Path,
) -> AggregateEvidence | str:
    """Resolve every requested metric and period, then execute gated SQL read-only."""

    if not is_enriched_catalog(catalog):
        return "当前数据源尚未提供多指标、时间范围或分组查询。"
    metric_names = tuple(intent.metric_names or ([intent.metric_name] if intent.metric_name else []))
    if not metric_names or len(metric_names) > 3:
        return "请指定 1 到 3 个已批准的指标。"
    if intent.scope or intent.secondary_intents:
        return "当前暂不支持指定业务范围或这组复合任务。"
    if len(intent.dimensions) > 1 or any(item not in {"国家", "商品编码"} for item in intent.dimensions):
        return "当前只支持按国家或商品编码单维度分组。"
    group_by = intent.dimensions[0] if intent.dimensions else None
    explicit_dates = set(re.findall(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)", question))
    if intent.date_range is not None:
        if intent.intent_type == "attribution":
            return "分组贡献分析请给出两个明确日期；当前不支持两个时间段对比。"
        periods = ((intent.date_range.start, intent.date_range.end),)
        if not explicit_dates.issubset({item.isoformat() for item in periods[0]}):
            return "识别出的日期范围与问题中的明确日期不一致；没有执行查询。"
    else:
        dates = intent.requested_dates()
        if not dates or len(dates) > 7 or dates != tuple(sorted(set(dates))):
            return "请提供 1 到 7 个互不重复且递增的明确日期。"
        if not explicit_dates.issubset({day.isoformat() for day in dates}):
            return "意图识别的日期与问题中明确写出的日期不一致；没有执行查询。"
        periods = tuple((day, day) for day in dates)
    if intent.intent_type == "attribution":
        if group_by is None or len(metric_names) != 1 or len(periods) != 2:
            return "分组贡献分析需要一个指标、一个分组维度和两个明确日期。"
    metrics = [search_metrics(name, catalog) for name in metric_names]
    if any(metric is None for metric in metrics):
        return "请求的指标不在已批准的 UCI 指标目录中；没有执行查询。"
    try:
        results = tuple(
            query_uci_aggregate(
                metric, start, end, database_path, group_by,
                candidate=build_aggregate_query(metric, start, end, group_by),
            )
            for metric in metrics for start, end in periods
        )
        return AggregateEvidence(results=results)
    except UCIDateOutOfRange as exc:
        return f"UCI 数据日期仅覆盖 {exc.first_date} 至 {exc.last_date}；没有返回数值。"
    except (UCIQueryError, ValueError):
        return "UCI 聚合查询未通过指标、SQL 或执行校验；没有返回数值。"


def answer_aggregate_query(evidence: AggregateEvidence) -> str:
    """Show verified totals and a bounded preview of groups for each result."""

    parts = []
    for result in evidence.results:
        period = result.start.isoformat() if result.start == result.end else (
            f"{result.start.isoformat()} 至 {result.end.isoformat()}"
        )
        detail = f"{period} 的{result.metric_name}为 {_display(result.total, result.unit)}"
        if result.group_by:
            ranked = sorted(result.groups, key=lambda item: (-item.value, item.group))
            preview = "、".join(
                f"{item.group} {_display(item.value, result.unit)}" for item in ranked[:5]
            )
            if len(ranked) > 5:
                remainder = sum(item.value for item in ranked[5:])
                preview += f"、其余 {len(ranked) - 5} 组 {_display(remainder, result.unit)}"
            detail += f"；按{result.group_by}分组：{preview or '无正向明细'}"
        parts.append(detail)
    return (
        "UCI Online Retail 公开数据：" + "；".join(parts) + "。"
        "仅计正向且数量、单价均为正的明细；金额不是已支付 GMV，行数不是订单数。"
    )


def answer_group_analysis(evidence: AggregateEvidence) -> str:
    """Attribute an observed total change to groups, without claiming causality."""

    if len(evidence.results) != 2:
        raise ValueError("分组分析需要两个日期的完整证据")
    previous, current = evidence.results
    if (previous.metric_name != current.metric_name or previous.unit != current.unit
            or previous.group_by is None or previous.group_by != current.group_by
            or previous.start != previous.end or current.start != current.end
            or previous.start >= current.start):
        raise ValueError("分组分析的证据不一致")
    previous_groups = {item.group: item.value for item in previous.groups}
    current_groups = {item.group: item.value for item in current.groups}
    deltas = {
        group: current_groups.get(group, 0) - previous_groups.get(group, 0)
        for group in previous_groups.keys() | current_groups.keys()
    }
    change = current.total - previous.total
    if sum(deltas.values()) != change:
        raise ValueError("分组贡献之和不等于总体变化")
    direction = "下降" if change < 0 else "上升" if change > 0 else "持平"
    if previous.total:
        rate = (Decimal(abs(change)) * 100 / Decimal(previous.total)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        rate_text = f"，变化率 {rate:.2f}%"
    else:
        rate_text = "，基期为零，变化率无法计算"
    ranked = sorted(deltas.items(), key=lambda item: (item[1], item[0]))
    decreased = -sum(delta for delta in deltas.values() if delta < 0)
    increased = sum(delta for delta in deltas.values() if delta > 0)
    reconciliation = (
        f"各下降组共减少 {_display(decreased, previous.unit)}，"
        f"各上升组共增加 {_display(increased, previous.unit)}；"
    )
    if change < 0:
        drivers = [(group, delta) for group, delta in ranked if delta < 0][:3]
        details = "；".join(
            f"{group}减少 {_display(-delta, previous.unit)}"
            f"（占总体净降幅 {Decimal(-delta) * 100 / Decimal(-change):.2f}%）"
            for group, delta in drivers
        )
    elif change > 0:
        drivers = [(group, delta) for group, delta in reversed(ranked) if delta > 0][:3]
        details = "；".join(
            f"{group}增加 {_display(delta, previous.unit)}"
            f"（占总体净增幅 {Decimal(delta) * 100 / Decimal(change):.2f}%）"
            for group, delta in drivers
        )
    else:
        details = "各组增减相互抵消" if decreased else "各组均无变化"
    offset_note = "不同组的增减可能互相抵消；" if decreased and increased else ""
    return (
        f"UCI Online Retail 公开数据：{previous.start} 的{previous.metric_name}"
        f"为 {_display(previous.total, previous.unit)}，{current.start} 为 "
        f"{_display(current.total, current.unit)}；总体{direction} "
        f"{_display(abs(change), previous.unit)}{rate_text}。"
        f"按{previous.group_by}拆解：{reconciliation}{details}。"
        f"这是分组数值对总体变化的贡献，{offset_note}不能单凭这些数据证明下降原因。"
    )
