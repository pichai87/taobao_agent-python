"""Plan task dependencies, then choose the expert responsible for the final result."""

from typing import Literal

from langchain_core.exceptions import ModelRateLimitError
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from taobao_agent.intent_agent import IntentResult
from taobao_agent.model import ChatModel


ExpertName = Literal["query", "analysis", "ops", "other"]
TaskName = Literal["table_meta", "lineage", "data_query", "analysis", "ops", "other"]


class TaskPlanProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tasks: list[TaskName] = Field(min_length=1, max_length=4)


def plan_tasks(intent: IntentResult) -> tuple[TaskName, ...] | None:
    """Close explicit intents over prerequisites instead of enumerating combinations."""

    intents = {intent.intent_type, *intent.secondary_intents}
    if "other" in intents:
        return ("other",) if len(intents) == 1 else None
    if "ops" in intents:
        return ("ops",) if len(intents) == 1 else None
    tasks: set[TaskName] = set()
    if "table_meta" in intents:
        tasks.add("table_meta")
    if "lineage" in intents:
        tasks.add("lineage")
    if "data_query" in intents or "attribution" in intents:
        tasks.add("data_query")
    if "attribution" in intents:
        tasks.add("analysis")
    return tuple(name for name in ("table_meta", "lineage", "data_query", "analysis") if name in tasks)


def plan_with_model(
    intent: IntentResult, question: str, model: ChatModel,
) -> tuple[tuple[TaskName, ...] | None, str]:
    """Ask the model only for compound work; accept only an approved dependency closure."""

    required = plan_tasks(intent)
    explicit_intents = {intent.intent_type, *intent.secondary_intents}
    may_need_meta = required == ("data_query",) and any(
        word in question for word in ("口径", "怎么计算", "如何计算", "哪个字段")
    )
    if required is None or (len(explicit_intents) < 2 and not may_need_meta):
        return required, "rule"
    try:
        response = model.invoke([
            SystemMessage(content=(
                "你是任务规划专家。根据问题和已识别意图输出 JSON：{\"tasks\":[...]}。"
                "只允许 table_meta、lineage、data_query、analysis；分析必须先取数。"
                "必须保留用户明确提出的任务；可补充用户明确问到的指标口径任务。"
                "不要生成 SQL、指标数值或额外意图。"
            )),
            HumanMessage(content=(
                f"用户问题：{question}\n主意图：{intent.intent_type}；"
                f"次要意图：{intent.secondary_intents}；最少需要：{list(required)}"
            )),
        ])
        if not isinstance(response.content, str):
            raise ValueError("计划不是 JSON 文本")
        proposal = TaskPlanProposal.model_validate_json(response.content)
        proposed = set(proposal.tasks)
        extra = proposed - set(required)
        may_add_meta = "table_meta" in extra and any(
            word in question for word in ("口径", "怎么计算", "如何计算", "哪个字段")
        )
        if len(proposed) != len(proposal.tasks) or not set(required) <= proposed:
            raise ValueError("计划漏任务或重复")
        if extra and not (extra == {"table_meta"} and may_add_meta):
            raise ValueError("计划含未批准任务")
        if "analysis" in proposed and "data_query" not in proposed:
            raise ValueError("分析缺少取数依赖")
        ordered = tuple(name for name in ("table_meta", "lineage", "data_query", "analysis") if name in proposed)
        return ordered, "llm"
    except (ModelRateLimitError, ValidationError, ValueError):
        return required, "rule"


def select_expert(intent: IntentResult, tasks: tuple[TaskName, ...] | None = None) -> ExpertName:
    """Select the expert responsible for the final answer."""

    planned = tasks if tasks is not None else plan_tasks(intent)
    if planned and "analysis" in planned:
        return "analysis"
    if planned and any(task in planned for task in ("data_query", "table_meta", "lineage")):
        return "query"
    if planned == ("ops",):
        return "ops"
    return "other"
