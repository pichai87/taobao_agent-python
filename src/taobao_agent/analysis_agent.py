"""Analyze complete Query evidence; never read the database from Analysis."""

import json
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal

from langchain_core.exceptions import ModelRateLimitError
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, ValidationError

from taobao_agent.model import ChatModel
from taobao_agent.query_agent import Observation, QueryEvidence, _display


class AnalysisPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mode: Literal["overall_change", "largest_daily_drop", "high_low"]


def _percent(numerator: Decimal, denominator: Decimal) -> str:
    if denominator == 0:
        return "无法计算（基期为零）"
    value = abs(numerator) * 100 / denominator
    return f"{value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):.2f}%"


def _group_contribution(first: Observation, last: Observation) -> str:
    if first.group_by is None:
        return ""
    before = {item.name: item.value for item in first.groups}
    after = {item.name: item.value for item in last.groups}
    changes = {name: after.get(name, 0) - before.get(name, 0) for name in before.keys() | after.keys()}
    overall = last.total - first.total
    if sum(changes.values()) != overall:
        raise ValueError("行业贡献与总体变化不一致")
    if overall == 0:
        return "各行业的变化合计为零；这是数值拆解，不能据此证明经营原因。"
    ranked = sorted(changes.items(), key=lambda pair: pair[1], reverse=overall > 0)
    relevant = [(name, delta) for name, delta in ranked if delta * overall > 0][:3]
    details = "；".join(
        f"{name}{'增加' if delta > 0 else '减少'} {_display(abs(delta), first.unit)}"
        f"（占总体净{'增' if overall > 0 else '降'}幅 {_percent(Decimal(delta), Decimal(abs(overall)))}）"
        for name, delta in relevant
    )
    return f"按行业拆解：{details}。这是数值贡献，不能单凭它证明经营原因。"


def _find(evidence: QueryEvidence, name: str, index: int) -> Observation | None:
    period = evidence.request.periods[index]
    return next((item for item in evidence.results if item.metric_name == name
                 and item.start == period.start and item.end == period.end), None)


def _funnel(evidence: QueryEvidence) -> str:
    if evidence.request.metric_names[0] != "GMV" or len(evidence.request.periods) != 2:
        return ""
    first_uv, last_uv = _find(evidence, "访客数", 0), _find(evidence, "访客数", 1)
    first_orders, last_orders = _find(evidence, "订单数", 0), _find(evidence, "订单数", 1)
    first_gmv, last_gmv = _find(evidence, "GMV", 0), _find(evidence, "GMV", 1)
    if not all((first_uv, last_uv, first_orders, last_orders, first_gmv, last_gmv)):
        return ""
    u0, u1 = Decimal(first_uv.total), Decimal(last_uv.total)
    o0, o1 = Decimal(first_orders.total), Decimal(last_orders.total)
    g0, g1 = Decimal(first_gmv.total), Decimal(last_gmv.total)
    if min(u0, u1, o0, o1) == 0:
        return "访客数或订单数为零，无法计算完整漏斗拆解。"
    c0, c1 = o0 / u0, o1 / u1
    a0, a1 = g0 / o0, g1 / o1
    effects = {
        "访客数": (u1 - u0) * c0 * a0,
        "转化率": u1 * (c1 - c0) * a0,
        "客单价": u1 * c1 * (a1 - a0),
    }
    if abs(sum(effects.values()) - (g1 - g0)) > Decimal("0.0001"):
        raise ValueError("漏斗拆解无法对齐 GMV")
    effects_text = "、".join(
        f"{name}{'增加' if value > 0 else '减少' if value < 0 else '不变'} "
        f"{_display(int(abs(value).quantize(Decimal('1'), rounding=ROUND_HALF_UP)), 'cents')}"
        for name, value in effects.items()
    )
    return (
        f"漏斗核算：GMV = 访客数 × 转化率 × 客单价；"
        f"访客数 {int(u0)}→{int(u1)} 人，转化率 {c0 * 100:.2f}%→{c1 * 100:.2f}%，"
        f"客单价 {_display(int(a0), 'cents')}→{_display(int(a1), 'cents')}。"
        f"按访客数、转化率、客单价的顺序做数值拆解：{effects_text}；"
        "拆解结果依赖计算顺序，不等于已经证实的因果原因。"
    )


def answer_analysis_question(
    question: str, evidence: QueryEvidence, model: ChatModel | None = None,
) -> str:
    """Let the model choose a supported view; calculate every value in Python."""

    if len(evidence.request.periods) < 2 or any(
        period.start != period.end for period in evidence.request.periods
    ):
        raise ValueError("分析需要至少两个明确的单日查询结果")
    metric_name = evidence.request.metric_names[0]
    values = [_find(evidence, metric_name, index) for index in range(len(evidence.request.periods))]
    if any(value is None for value in values):
        raise ValueError("分析缺少查询证据")
    mode = "overall_change"
    if model is not None:
        try:
            response = model.invoke([
                SystemMessage(content=(
                    "你是分析专家。只选择一个 JSON 计划："
                    "overall_change（变化或问原因）、largest_daily_drop（最大相邻日下降）、"
                    "high_low（最高和最低）。输出 {\"mode\":\"...\"}，不要编造数值或原因。"
                )),
                HumanMessage(content=(
                    f"用户问题：{question}\n已核实金额："
                    f"{json.dumps([{'date': item.start.isoformat(), 'value': item.total} for item in values], ensure_ascii=False)}"
                )),
            ])
            if not isinstance(response.content, str):
                raise ValueError("分析计划不是 JSON 文本")
            mode = AnalysisPlan.model_validate_json(response.content).mode
        except (ModelRateLimitError, ValidationError, ValueError):
            pass
    first, last = values[0], values[-1]
    delta = last.total - first.total
    direction = "下降" if delta < 0 else "上升" if delta > 0 else "持平"
    observations = "、".join(
        f"{item.start.isoformat()} {_display(item.total, item.unit)}" for item in values
    )
    parts = [
        f"教学合成数据（百亿补贴）：{metric_name} 为 {observations}；"
        f"首末{direction} {_display(abs(delta), first.unit)}，"
        f"变化率 {_percent(Decimal(delta), Decimal(first.total))}。"
    ]
    if mode == "high_low":
        high = max(values, key=lambda item: item.total)
        low = min(values, key=lambda item: item.total)
        parts.append(f"最高为 {high.start} 的 {_display(high.total, high.unit)}；"
                     f"最低为 {low.start} 的 {_display(low.total, low.unit)}。")
    if mode == "largest_daily_drop":
        changes = [(a, b, a.total - b.total) for a, b in zip(values, values[1:])]
        a, b, drop = max(changes, key=lambda item: item[2])
        parts.append(
            f"最大相邻日下降发生在 {a.start}→{b.start}，下降 {_display(drop, a.unit)}。"
            if drop > 0 else "指定日期间没有相邻日下降。"
        )
    if mode == "overall_change":
        funnel = _funnel(evidence)
        if funnel:
            parts.append(funnel)
        if evidence.request.group_by:
            parts.append(_group_contribution(first, last))
    if not any("不能" in part or "不等于" in part for part in parts):
        parts.append("数值变化本身不能证明经营原因。")
    return " ".join(parts)
