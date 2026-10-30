"""The first Supervisor decision: choose one expert from a validated intent."""

from typing import Literal

from taobao_agent.intent_agent import IntentResult


ExpertName = Literal["query", "analysis", "other"]
TaskName = Literal["table_meta", "data_query", "analysis", "other"]


def plan_tasks(intent: IntentResult) -> tuple[TaskName, ...] | None:
    """Plan only the task combinations supported by the current experts."""

    if not intent.secondary_intents:
        if intent.intent_type == "attribution":
            return ("data_query", "analysis")
        return (intent.intent_type,)
    if (
        len(intent.secondary_intents) == 1
        and {intent.intent_type, intent.secondary_intents[0]} == {"table_meta", "data_query"}
    ):
        return ("table_meta", "data_query")
    return None


def select_expert(intent: IntentResult) -> ExpertName:
    """Select the expert responsible for the final answer."""

    if intent.intent_type in {"data_query", "table_meta"}:
        return "query"
    if intent.intent_type == "attribution":
        return "analysis"
    return "other"
