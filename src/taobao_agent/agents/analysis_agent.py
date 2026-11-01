"""Analyze complete Query evidence; never read the database from Analysis."""

import json
from decimal import Decimal
from typing import Literal

from langchain_core.exceptions import ModelRateLimitError
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, ValidationError

from taobao_agent.model import ChatModel
from taobao_agent.agents.harness import RunHarness
from taobao_agent.agents.query_agent import QueryEvidence, _display
from taobao_agent.tools.analysis import _find, _funnel, _group_contribution, _percent


class AnalysisPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mode: Literal["overall_change", "largest_daily_drop", "high_low"]


def answer_analysis_question(
    question: str, evidence: QueryEvidence, model: ChatModel | None = None,
    harness: RunHarness | None = None,
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
            messages = [
                SystemMessage(content=(
                    "你是分析专家。只选择一个 JSON 计划："
                    "overall_change（变化或问原因）、largest_daily_drop（最大相邻日下降）、"
                    "high_low（最高和最低）。输出 {\"mode\":\"...\"}，不要编造数值或原因。"
                )),
                HumanMessage(content=(
                    f"用户问题：{question}\n已核实金额："
                    f"{json.dumps([{'date': item.start.isoformat(), 'value': item.total} for item in values], ensure_ascii=False)}"
                )),
            ]
            response = (harness.call("analysis", "choose_view", lambda: model.invoke(messages))
                        if harness else model.invoke(messages))
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
        funnel = (harness.call("analysis", "funnel_reconcile", lambda: _funnel(evidence))
                  if harness else _funnel(evidence))
        if funnel:
            parts.append(funnel)
        if evidence.request.group_by:
            parts.append(harness.call("analysis", "segment_contribution",
                                      lambda: _group_contribution(first, last))
                         if harness else _group_contribution(first, last))
    if not any("不能" in part or "不等于" in part for part in parts):
        parts.append("数值变化本身不能证明经营原因。")
    return " ".join(parts)
