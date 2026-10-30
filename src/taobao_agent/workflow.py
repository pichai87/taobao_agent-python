"""The active LangGraph workflow: understand, choose, and run available experts."""

import argparse
import json
import os
from datetime import date
from pathlib import Path
from typing import NotRequired, Sequence, TypedDict

from langchain_core.exceptions import ModelRateLimitError
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from taobao_agent.analysis_agent import MetricSeriesEvidence, answer_analysis_question
from taobao_agent.intent_agent import IntentResult, run_intent_once
from taobao_agent.metric_catalog import load_metric_catalog
from taobao_agent.metric_search import MetricDefinition
from taobao_agent.model import ChatModel, ToolCallingModel, create_qwen_model
from taobao_agent.other_agent import answer_other
from taobao_agent.query_agent import answer_query, answer_table_meta, fetch_metric_series
from taobao_agent.supervisor import ExpertName, TaskName, plan_tasks, select_expert
from taobao_agent.uci_online_retail import UCI_CATALOG_PATH


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
    model: ChatModel,
    reference_date: date,
    metric_catalog: Sequence[MetricDefinition] = (),
    uci_db_path: Path | None = None,
    query_model: ToolCallingModel | None = None,
    analysis_model: ChatModel | None = None,
):
    """Compile the main graph with Query, Analysis, and Other branches."""

    def intent_node(state: WorkflowState) -> dict[str, dict[str, object]]:
        intent = run_intent_once(state["question"], model, reference_date)
        return {"intent": intent.model_dump(mode="json")}

    def supervisor_node(state: WorkflowState) -> dict[str, object]:
        intent = IntentResult.model_validate(state["intent"])
        tasks = plan_tasks(intent)
        if tasks is None:
            return {
                "next_expert": select_expert(intent),
                "task_plan": [],
                "answer": "当前尚不支持这组复合任务；请先分别提问。",
            }
        return {"next_expert": select_expert(intent), "task_plan": list(tasks)}

    def other_node(state: WorkflowState) -> dict[str, str]:
        return {"answer": answer_other(state["question"])}

    def query_node(state: WorkflowState) -> dict[str, object]:
        intent = IntentResult.model_validate(state["intent"])
        if state["task_plan"] == ["table_meta", "data_query"]:
            single = intent.model_copy(update={"secondary_intents": []})
            metadata = answer_table_meta(single, metric_catalog, uci_db_path)
            amount = answer_query(
                single, metric_catalog, uci_db_path=uci_db_path,
                query_model=query_model, question=state["question"],
            )
            return {"answer": f"指标口径：{metadata}\n查询结果：{amount}"}
        if state["task_plan"] == ["table_meta"]:
            return {"answer": answer_table_meta(intent, metric_catalog, uci_db_path)}
        if state["next_expert"] == "analysis":
            result = fetch_metric_series(
                intent, metric_catalog, uci_db_path=uci_db_path,
                query_model=query_model, question=state["question"],
            )
            if isinstance(result, str):
                return {"answer": result, "query_evidence": None}
            return {"query_evidence": result.model_dump(mode="json")}
        return {
            "answer": answer_query(
                intent, metric_catalog, uci_db_path=uci_db_path,
                query_model=query_model, question=state["question"],
            )
        }

    def analysis_node(state: WorkflowState) -> dict[str, str]:
        evidence = MetricSeriesEvidence.model_validate(state["query_evidence"])
        return {"answer": answer_analysis_question(state["question"], evidence, analysis_model)}

    def route_after_supervisor(state: WorkflowState) -> str:
        if not state["task_plan"]:
            return "done"
        if state["next_expert"] == "analysis":
            return "query"
        return state["next_expert"]

    def route_after_query(state: WorkflowState) -> str:
        if state["next_expert"] == "analysis" and state.get("query_evidence") is not None:
            return "analysis"
        return "done"

    graph = StateGraph(WorkflowState)
    graph.add_node("intent", intent_node)
    graph.add_node("supervisor", supervisor_node)
    graph.add_node("other", other_node)
    graph.add_node("query", query_node)
    graph.add_node("analysis", analysis_node)
    graph.add_edge(START, "intent")
    graph.add_edge("intent", "supervisor")
    graph.add_conditional_edges(
        "supervisor",
        route_after_supervisor,
        {"other": "other", "query": "query", "analysis": "query", "done": END},
    )
    graph.add_edge("other", END)
    graph.add_conditional_edges(
        "query", route_after_query, {"analysis": "analysis", "done": END}
    )
    graph.add_edge("analysis", END)
    return graph.compile()


def run_workflow(
    question: str,
    model: ChatModel,
    reference_date: date,
    metric_catalog: Sequence[MetricDefinition] = (),
    uci_db_path: Path | None = None,
    query_model: ToolCallingModel | None = None,
    analysis_model: ChatModel | None = None,
) -> WorkflowResult:
    """Run the active path and return the selected expert's answer."""

    if not question.strip():
        raise ValueError("Question must not be empty")
    state = build_workflow(
        model, reference_date, metric_catalog, uci_db_path, query_model, analysis_model
    ).invoke(
        {"question": question.strip()}
    )
    return WorkflowResult(
        intent=state["intent"], next_expert=state["next_expert"], answer=state.get("answer")
    )


def main() -> None:
    """Main CLI entry point for the current workflow."""

    parser = argparse.ArgumentParser(description="电商分析 Agent：意图识别、专家选择与可选本地数据查询和比较")
    parser.add_argument("question", help="输入一个问题")
    args = parser.parse_args()

    try:
        catalog_path = os.environ.get("TAOBAO_METRIC_CATALOG_PATH")
        uci_path_text = os.environ.get("TAOBAO_UCI_DB_PATH")
        if os.environ.get("TAOBAO_DEMO_DATA") not in (None, "", "0"):
            raise ValueError("TAOBAO_DEMO_DATA 教学模式已移除；请使用 TAOBAO_UCI_DB_PATH 查询公开数据")
        if uci_path_text and catalog_path:
            raise ValueError("UCI 公开数据模式不能同时设置自定义指标目录")
        uci_db_path = Path(uci_path_text) if uci_path_text else None
        if uci_db_path is not None and not uci_db_path.is_file():
            raise ValueError(f"UCI 数据库文件不存在：{uci_db_path}")
        if uci_db_path is not None:
            metric_catalog = load_metric_catalog(UCI_CATALOG_PATH)
        else:
            metric_catalog = load_metric_catalog(Path(catalog_path)) if catalog_path else ()
        model = create_qwen_model()
    except (RuntimeError, ValueError) as exc:
        parser.exit(2, f"{exc}\n")

    try:
        result = run_workflow(
            args.question, model, date.today(), metric_catalog, uci_db_path,
            query_model=model, analysis_model=model,
        )
    except ModelRateLimitError:
        parser.exit(3, "百炼返回 429：当前模型请求受到限流，本次任务未完成。\n")
    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False))
