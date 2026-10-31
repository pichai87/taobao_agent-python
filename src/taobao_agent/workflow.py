"""One LangGraph workflow with one commerce Query and one Analysis evidence path."""

import argparse
import json
import os
from datetime import date
from pathlib import Path
from typing import NotRequired, Sequence, TypedDict

from langchain_core.exceptions import ModelRateLimitError
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from taobao_agent.analysis_agent import answer_analysis_question
from taobao_agent.commerce_data import CATALOG_PATH, DEFAULT_DB_PATH
from taobao_agent.intent_agent import IntentResult, run_intent_once
from taobao_agent.metric_catalog import load_metric_catalog
from taobao_agent.metric_search import MetricDefinition
from taobao_agent.model import ChatModel, ToolCallingModel, create_qwen_model
from taobao_agent.other_agent import answer_other
from taobao_agent.query_agent import QueryEvidence, answer_query, answer_table_meta, fetch_query_evidence
from taobao_agent.supervisor import ExpertName, TaskName, plan_tasks, select_expert


class WorkflowState(TypedDict):
    question: str
    intent: NotRequired[dict[str, object]]
    next_expert: NotRequired[ExpertName]
    task_plan: NotRequired[list[TaskName]]
    query_evidence: NotRequired[dict[str, object] | None]
    answer: NotRequired[str]


class WorkflowResult(BaseModel):
    intent: IntentResult
    next_expert: ExpertName
    answer: str | None = None


def build_workflow(
    model: ChatModel, reference_date: date,
    metric_catalog: Sequence[MetricDefinition] | None = None,
    database_path: Path = DEFAULT_DB_PATH,
    query_model: ToolCallingModel | None = None,
    analysis_model: ChatModel | None = None,
):
    """Compile Intent → Supervisor → Query → optional Analysis once."""

    catalog = tuple(metric_catalog) if metric_catalog is not None else load_metric_catalog(CATALOG_PATH)

    def intent_node(state: WorkflowState) -> dict[str, dict[str, object]]:
        result = run_intent_once(state["question"], model, reference_date)
        return {"intent": result.model_dump(mode="json")}

    def supervisor_node(state: WorkflowState) -> dict[str, object]:
        intent = IntentResult.model_validate(state["intent"])
        tasks = plan_tasks(intent)
        if tasks is None:
            return {
                "next_expert": select_expert(intent), "task_plan": [],
                "answer": "当前尚不支持这组复合任务；请先分别提问。",
            }
        return {"next_expert": select_expert(intent), "task_plan": list(tasks)}

    def other_node(state: WorkflowState) -> dict[str, str]:
        return {"answer": answer_other(state["question"])}

    def query_node(state: WorkflowState) -> dict[str, object]:
        intent = IntentResult.model_validate(state["intent"])
        tasks = state["task_plan"]
        if tasks == ["table_meta"]:
            return {"answer": answer_table_meta(intent, catalog, database_path)}
        metadata = None
        if tasks == ["table_meta", "data_query"]:
            metadata = answer_table_meta(intent, catalog, database_path)
            intent = intent.model_copy(update={"secondary_intents": []})
        query_model_for_task = query_model or model
        if not hasattr(query_model_for_task, "bind_tools"):
            return {"answer": "取数模型不支持工具调用；没有执行查询。", "query_evidence": None}
        result = fetch_query_evidence(
            intent, state["question"], catalog, database_path, query_model_for_task
        )
        if isinstance(result, str):
            return {"answer": result, "query_evidence": None}
        if state["next_expert"] == "analysis":
            return {"query_evidence": result.model_dump(mode="json")}
        answer = answer_query(result)
        if metadata:
            answer = f"指标口径：{metadata}\n查询结果：{answer}"
        return {"answer": answer}

    def analysis_node(state: WorkflowState) -> dict[str, str]:
        evidence = QueryEvidence.model_validate(state["query_evidence"])
        return {"answer": answer_analysis_question(
            state["question"], evidence, analysis_model or model
        )}

    def route_after_supervisor(state: WorkflowState) -> str:
        if not state["task_plan"]:
            return "done"
        if state["next_expert"] == "analysis":
            return "query"
        return state["next_expert"]

    def route_after_query(state: WorkflowState) -> str:
        return "analysis" if state["next_expert"] == "analysis" and state.get("query_evidence") else "done"

    graph = StateGraph(WorkflowState)
    graph.add_node("intent", intent_node)
    graph.add_node("supervisor", supervisor_node)
    graph.add_node("query", query_node)
    graph.add_node("analysis", analysis_node)
    graph.add_node("other", other_node)
    graph.add_edge(START, "intent")
    graph.add_edge("intent", "supervisor")
    graph.add_conditional_edges(
        "supervisor", route_after_supervisor,
        {"other": "other", "query": "query", "analysis": "query", "done": END},
    )
    graph.add_conditional_edges("query", route_after_query, {"analysis": "analysis", "done": END})
    graph.add_edge("analysis", END)
    graph.add_edge("other", END)
    return graph.compile()


def run_workflow(
    question: str, model: ChatModel, reference_date: date,
    metric_catalog: Sequence[MetricDefinition] | None = None,
    database_path: Path = DEFAULT_DB_PATH,
    query_model: ToolCallingModel | None = None,
    analysis_model: ChatModel | None = None,
) -> WorkflowResult:
    if not question.strip():
        raise ValueError("Question must not be empty")
    state = build_workflow(
        model, reference_date, metric_catalog, database_path, query_model, analysis_model
    ).invoke({"question": question.strip()})
    return WorkflowResult(
        intent=state["intent"], next_expert=state["next_expert"], answer=state.get("answer")
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="电商分析 Agent：单一合成数据源与统一取数链")
    parser.add_argument("question", help="输入问题")
    args = parser.parse_args()
    if os.environ.get("TAOBAO_UCI_DB_PATH") or os.environ.get("TAOBAO_DEMO_DATA"):
        parser.exit(2, "旧数据源开关已移除；请取消 TAOBAO_UCI_DB_PATH / TAOBAO_DEMO_DATA。\n")
    database_path = Path(os.environ.get("TAOBAO_DB_PATH", str(DEFAULT_DB_PATH)))
    if not database_path.is_file():
        parser.exit(2, "请先运行 uv run taobao-seed-commerce 创建合成电商数据库。\n")
    try:
        model = create_qwen_model()
        catalog = load_metric_catalog(CATALOG_PATH)
        result = run_workflow(
            args.question, model, date.today(), catalog, database_path,
            query_model=model, analysis_model=model,
        )
    except ModelRateLimitError:
        parser.exit(3, "百炼返回 429：当前请求受限流，本次任务未完成。\n")
    except (ValueError, RuntimeError) as exc:
        parser.exit(2, f"运行失败：{exc}\n")
    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False))
