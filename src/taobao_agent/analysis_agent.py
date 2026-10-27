"""Compare two daily totals while preserving where the numbers came from."""

import re
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from taobao_agent.intent_agent import IntentResult


class ComparisonEvidence(BaseModel):
    """Two totals from either the approved UCI query or the current user message."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    metric_name: str
    prev: date
    curr: date
    previous_minor: int = Field(ge=0)
    current_minor: int = Field(ge=0)
    source: Literal["uci", "user"]
    unit: Literal["pence", "fen"]

    @model_validator(mode="after")
    def check_source_unit(self) -> "ComparisonEvidence":
        if self.source == "uci" and self.unit != "pence":
            raise ValueError("UCI evidence must use pence")
        if self.prev == self.curr:
            raise ValueError("comparison needs two distinct dates")
        return self


_STATED_AMOUNT = re.compile(
    r"(?P<day>\d{4}-\d{2}-\d{2})[^\d]{0,30}?"
    r"(?P<amount>\d{1,12}(?:\.\d{1,2})?)\s*(?P<unit>英镑|GBP|£|元)",
    re.IGNORECASE,
)


def extract_user_comparison(question: str, intent: IntentResult) -> ComparisonEvidence | None:
    """Use only two explicit, same-unit amounts stated in the current question."""

    if (
        intent.intent_type != "attribution" or not intent.metric_name
        or intent.prev is None or intent.curr is None
        or intent.scope or intent.dimensions or intent.secondary_intents
    ):
        return None
    matches = list(_STATED_AMOUNT.finditer(question))
    if len(matches) != 2:
        return None

    values: dict[date, tuple[int, Literal["fen", "pence"]]] = {}
    for match in matches:
        if intent.metric_name.casefold() not in match.group(0).casefold():
            return None
        try:
            business_date = date.fromisoformat(match.group("day"))
        except ValueError:
            return None
        if business_date in values:
            return None
        label = match.group("unit").upper()
        unit: Literal["fen", "pence"] = "fen" if label == "元" else "pence"
        amount_minor = int(Decimal(match.group("amount")) * 100)
        values[business_date] = (amount_minor, unit)

    if set(values) != {intent.prev, intent.curr}:
        return None
    previous, previous_unit = values[intent.prev]
    current, current_unit = values[intent.curr]
    if previous_unit != current_unit:
        return None
    return ComparisonEvidence(
        metric_name=intent.metric_name, prev=intent.prev, curr=intent.curr,
        previous_minor=previous, current_minor=current,
        source="user", unit=previous_unit,
    )


def _money(minor_units: int) -> str:
    """Display an integer number of cents/pence without floating-point rounding."""

    return f"{minor_units // 100}.{minor_units % 100:02d}"


def answer_analysis(evidence: ComparisonEvidence) -> str:
    """Explain a comparison; never claim a cause from two totals."""

    if evidence.source == "user":
        source = "用户在本轮问题中提供的数据（未经数据库核实）"
        caveat = "仅凭两个总额不能判断变化原因。"
    else:
        source = "UCI Online Retail 公开数据"
        caveat = "这是排除取消、非正数量和非正单价后的交易明细金额，不是已支付 GMV；仅凭总额不能判断变化原因。"
    unit = "元" if evidence.unit == "fen" else "英镑"

    previous = evidence.previous_minor
    current = evidence.current_minor
    difference = current - previous
    direction = "下降" if difference < 0 else "上升" if difference > 0 else "持平"
    if previous == 0:
        change = "基期金额为 0，变化率无法计算"
    else:
        rate = (Decimal(abs(difference)) * 100 / Decimal(previous)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        change = f"变化率 {rate:.2f}%"
    return (
        f"{source}：{evidence.prev.isoformat()} 的{evidence.metric_name}为 {_money(previous)} {unit}，"
        f"{evidence.curr.isoformat()} 为 {_money(current)} {unit}；"
        f"{direction} {_money(abs(difference))} {unit}，{change}。{caveat}"
    )
