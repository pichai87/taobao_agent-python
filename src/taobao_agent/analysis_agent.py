"""Choose an analysis task from verified evidence; calculate values in Python."""

import json
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal

from langchain_core.exceptions import ModelRateLimitError
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from taobao_agent.daily_metric_tool import DailyMetricValue
from taobao_agent.model import ChatModel


class AnalysisPlan(BaseModel):
    """The only analytical choice an LLM may make for the current source."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    mode: Literal["overall_change", "largest_daily_drop", "high_low"]


class MetricSeriesEvidence(BaseModel):
    """One metric, one source and a nonempty set of dated observations."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    metric_name: str
    values: tuple[DailyMetricValue, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def check_series(self) -> "MetricSeriesEvidence":
        dates = [value.business_date for value in self.values]
        if dates != sorted(set(dates)):
            raise ValueError("series dates must be distinct and increasing")
        if any(value.metric_name != self.metric_name for value in self.values):
            raise ValueError("series contains a different metric")
        if len({(value.source, value.unit) for value in self.values}) != 1:
            raise ValueError("series contains mixed sources or units")
        return self


def _money(minor_units: int) -> str:
    return f"{minor_units // 100}.{minor_units % 100:02d}"


def answer_analysis(evidence: MetricSeriesEvidence) -> str:
    """Report each observed amount and the first-to-last change."""

    values = evidence.values
    if len(values) < 2:
        raise ValueError("analysis requires at least two dates")
    first, last = values[0], values[-1]
    difference = last.amount_minor - first.amount_minor
    direction = "下降" if difference < 0 else "上升" if difference > 0 else "持平"
    if first.amount_minor == 0:
        change = "基期金额为 0，变化率无法计算"
    else:
        rate = (Decimal(abs(difference)) * 100 / Decimal(first.amount_minor)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        change = f"变化率 {rate:.2f}%"

    amounts = "，".join(
        f"{value.business_date.isoformat()}{' 的' + evidence.metric_name + '为' if index == 0 else ' 为'} "
        f"{_money(value.amount_minor)} 英镑"
        for index, value in enumerate(values)
    )
    period = "" if len(values) == 2 else "首末"
    adjacent = ""
    if len(values) > 2:
        steps = []
        for previous, current in zip(values, values[1:]):
            delta = current.amount_minor - previous.amount_minor
            movement = "下降" if delta < 0 else "上升" if delta > 0 else "持平"
            steps.append(
                f"{previous.business_date.isoformat()}→{current.business_date.isoformat()} "
                f"{movement} {_money(abs(delta))} 英镑"
            )
        adjacent = "；相邻日期变化：" + "，".join(steps)
    return (
        f"UCI Online Retail 公开数据：{amounts}；{period}{direction} "
        f"{_money(abs(difference))} 英镑，{change}{adjacent}。"
        "这是排除取消、非正数量和非正单价后的交易明细金额，不是已支付 GMV；"
        "仅凭总额不能判断变化原因。"
    )


def answer_analysis_question(
    question: str, evidence: MetricSeriesEvidence, model: ChatModel | None = None,
) -> str:
    """Let the model select a supported analysis, then calculate and phrase it locally."""

    base = answer_analysis(evidence)
    if model is None:
        return base
    facts = [
        {"date": value.business_date.isoformat(), "amount_pence": value.amount_minor}
        for value in evidence.values
    ]
    try:
        response = model.invoke([
            SystemMessage(content=(
                "你是分析专家。仅从三种任务中选择用户最想看的分析方式："
                "overall_change（首末变化或问原因）、largest_daily_drop（最大相邻日下降）、"
                "high_low（最高和最低日期）。只输出 JSON 对象 {\"mode\": \"...\"}；"
                "不要输出结论、金额或原因，程序会根据已核实证据计算。"
            )),
            HumanMessage(content=(
                f"用户问题：{question}\n已核实证据："
                f"{json.dumps(facts, ensure_ascii=False)}"
            )),
        ])
        if not isinstance(response.content, str):
            raise ValueError("analysis model did not return JSON text")
        plan = AnalysisPlan.model_validate_json(response.content)
    except (ModelRateLimitError, ValidationError, ValueError):
        return base

    if plan.mode == "overall_change":
        return base
    if plan.mode == "largest_daily_drop":
        changes = [
            (previous, current, previous.amount_minor - current.amount_minor)
            for previous, current in zip(evidence.values, evidence.values[1:])
        ]
        previous, current, drop = max(changes, key=lambda item: item[2])
        if drop <= 0:
            return base + " 指定日期之间没有相邻日下降。"
        return (
            base + f" 最大相邻日下降发生在 {previous.business_date.isoformat()}→"
            f"{current.business_date.isoformat()}，下降 {_money(drop)} 英镑。"
        )
    highest = max(evidence.values, key=lambda item: item.amount_minor)
    lowest = min(evidence.values, key=lambda item: item.amount_minor)
    return (
        base + f" 最高为 {highest.business_date.isoformat()} 的 {_money(highest.amount_minor)} 英镑；"
        f"最低为 {lowest.business_date.isoformat()} 的 {_money(lowest.amount_minor)} 英镑。"
    )
