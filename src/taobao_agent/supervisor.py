"""The first Supervisor decision: choose one expert from a validated intent."""

from typing import Literal

from taobao_agent.intent_agent import IntentResult


ExpertName = Literal["query", "analysis", "other"]


def select_expert(intent: IntentResult) -> ExpertName:
    """Select one expert; multi-intent plans come later."""

    if intent.intent_type in {"data_query", "table_meta"}:
        return "query"
    if intent.intent_type == "attribution":
        return "analysis"
    return "other"
