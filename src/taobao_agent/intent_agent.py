"""Intent extraction used by the main workflow and the diagnostic CLI."""

import argparse
import json
import re
from datetime import date, timedelta
from typing import Literal

from langchain_core.exceptions import ModelRateLimitError
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from taobao_agent.model import ChatModel, create_qwen_model


IntentType = Literal["data_query", "attribution", "table_meta", "other"]


class IntentResult(BaseModel):
    """Small, validated subset of the article's intent parameter package."""

    model_config = ConfigDict(extra="forbid")

    intent_type: IntentType
    secondary_intents: list[IntentType]
    confidence: float = Field(ge=0, le=1)
    metric_name: str | None
    metric_field: str | None
    time_label: str | None
    curr: date | None
    prev: date | None
    dates: list[date] = Field(default_factory=list)
    scope: str | None
    dimensions: list[str]
    source: Literal["llm", "rule"] = "llm"

    @field_validator("curr", "prev", mode="before")
    @classmethod
    def require_iso_date(cls, value: object) -> date | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("date must be an ISO YYYY-MM-DD string")
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError("date must be an ISO YYYY-MM-DD string")
        return parsed

    @field_validator("dates", mode="before")
    @classmethod
    def require_iso_dates(cls, value: object) -> list[str]:
        if not isinstance(value, list):
            raise ValueError("dates must be a list of ISO YYYY-MM-DD strings")
        for item in value:
            if not isinstance(item, str) or date.fromisoformat(item).isoformat() != item:
                raise ValueError("dates must contain ISO YYYY-MM-DD strings")
        return value

    @field_validator("metric_field")
    @classmethod
    def reject_unverified_field(cls, value: str | None) -> None:
        if value is not None:
            raise ValueError("metric_field requires a verified semantic model")
        return None

    @model_validator(mode="after")
    def check_secondary_intents(self) -> "IntentResult":
        if len(set(self.secondary_intents)) != len(self.secondary_intents):
            raise ValueError("secondary_intents contains duplicates")
        if self.intent_type in self.secondary_intents:
            raise ValueError("primary intent cannot also be secondary")
        if len(set(self.dates)) != len(self.dates):
            raise ValueError("dates contains duplicates")
        if self.dates:
            if self.prev is not None and self.dates[0] != self.prev:
                raise ValueError("prev must match the first requested date")
            if self.curr is not None and self.dates[-1] != self.curr:
                raise ValueError("curr must match the last requested date")
        return self

    def requested_dates(self) -> tuple[date, ...]:
        """Keep the learned curr/prev contract while allowing longer date series."""

        if self.dates:
            return tuple(self.dates)
        if self.prev is not None and self.curr is not None:
            return self.prev, self.curr
        return (self.curr,) if self.curr is not None else ()


def run_intent_once(question: str, model: ChatModel, reference_date: date) -> IntentResult:
    """Call the model once, or use a narrow rule if that call is rate-limited."""

    if not question.strip():
        raise ValueError("Question must not be empty")
    system_prompt = (
        "你是电商数据分析助手的意图识别入口。只输出一个 JSON 对象，不回答问题，不编造数据。"
        "字段必须恰好为 intent_type, secondary_intents, confidence, metric_name, "
        "metric_field, time_label, curr, prev, dates, scope, dimensions。"
        "intent_type 和 secondary_intents 只允许 data_query（取数）、attribution（波动归因）、"
        "table_meta（表结构）、other（其他）。主意图放 intent_type；只有明确的第二任务才放入"
        "secondary_intents，否则给空数组。confidence 为 0 到 1 的数字。"
        "metric_name 写用户提到的业务指标名称；没有则为 null。当前没有语义模型，"
        "metric_field 必须为 null，不能猜测数据库字段。"
        "time_label 保留用户的时间表述；curr 是主查询或最后一个日期，prev 是比较基准日期；"
        "dates 按比较基准到最终日期列出用户明确要求的所有日期，单日或双日也可以列出；"
        "若 dates 非空，首项须与非空 prev 一致，末项须与非空 curr 一致。"
        "日期用 YYYY-MM-DD 字符串，没有明确或可解析的日期就填 null，dates 没有则为空数组。"
        "scope 只写用户明确指定的范围，否则为 null。dimensions 是用户明确要求的分组维度数组，"
        "没有则为空数组。其他缺失字段均用 null。"
        f"今天的参考日期是 {reference_date.isoformat()}，用于解析昨天、前天等相对日期。"
    )
    try:
        response = model.invoke(
            [SystemMessage(content=system_prompt), HumanMessage(content=question.strip())]
        )
    except ModelRateLimitError:
        fallback = rule_fallback(question, reference_date)
        if fallback is None:
            raise
        return fallback
    if not isinstance(response.content, str):
        raise ValueError("Model response must be a JSON string")
    result = IntentResult.model_validate_json(response.content)
    return result.model_copy(update={"source": "llm"})


def rule_fallback(question: str, reference_date: date) -> IntentResult | None:
    """Recognize only three explicit GMV phrasings; unknown questions stay unknown."""

    normalized = re.sub(r"[\s?？。]", "", question).upper()
    if normalized in {"昨天GMV多少", "昨天GMV是多少"}:
        intent_type: IntentType = "data_query"
        prev = None
        time_label = "昨天"
        dimensions: list[str] = []
    elif normalized == "昨天GMV比前天为什么下降":
        intent_type = "attribution"
        prev = reference_date - timedelta(days=2)
        time_label = "昨天、前天"
        dimensions = []
    elif normalized in {"按行业看昨天GMV", "按行业看昨天的GMV"}:
        intent_type = "data_query"
        prev = None
        time_label = "昨天"
        dimensions = ["行业"]
    else:
        return None

    return IntentResult(
        intent_type=intent_type,
        secondary_intents=[],
        confidence=0.0,
        metric_name="GMV",
        metric_field=None,
        time_label=time_label,
        curr=(reference_date - timedelta(days=1)).isoformat(),
        prev=prev.isoformat() if prev is not None else None,
        dates=[],
        scope=None,
        dimensions=dimensions,
        source="rule",
    )


def main() -> None:
    """CLI entry point for inspecting the current intent result."""

    parser = argparse.ArgumentParser(description="识别电商问题的意图和参数")
    parser.add_argument("question", help="输入一个问题")
    args = parser.parse_args()

    try:
        model = create_qwen_model()
    except RuntimeError as exc:
        parser.exit(2, f"{exc}\n")

    try:
        result = run_intent_once(args.question, model, date.today())
    except ModelRateLimitError:
        parser.exit(3, "百炼返回 429：当前模型请求受到限流。请稍后重试。\n")
    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False))
