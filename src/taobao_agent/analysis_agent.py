"""Describe a verified daily metric series without inventing causes."""

from decimal import Decimal, ROUND_HALF_UP

from pydantic import BaseModel, ConfigDict, Field, model_validator

from taobao_agent.daily_metric_tool import DailyMetricValue


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
