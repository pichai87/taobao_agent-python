"""One LangGraph workflow with one commerce Query and one Analysis evidence path."""

import argparse
import json
import os
import re
import sqlite3
from datetime import date
from pathlib import Path
from typing import NotRequired, Sequence, TypedDict

from langchain_core.exceptions import ModelRateLimitError
from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.sqlite import SqliteSaver
from pydantic import BaseModel

from taobao_agent.analysis_agent import answer_analysis_question
from taobao_agent.commerce_data import CATALOG_PATH, DEFAULT_DB_PATH
from taobao_agent.intent_agent import IntentResult, run_intent_once
from taobao_agent.harness import RunHarness, ToolUnavailableError
from taobao_agent.knowledge import load_knowledge
from taobao_agent.lineage import describe_lineage
from taobao_agent.metric_catalog import load_metric_catalog
from taobao_agent.metric_search import MetricDefinition
from taobao_agent.model import ChatModel, ToolCallingModel, create_qwen_model
from taobao_agent.other_agent import answer_other
from taobao_agent.ops_agent import answer_ops
from taobao_agent.ops_store import DEFAULT_OPS_PATH
from taobao_agent.query_agent import QueryEvidence, answer_query, answer_table_meta, fetch_query_evidence
from taobao_agent.supervisor import ExpertName, TaskName, plan_with_model, select_expert


class WorkflowState(TypedDict):
    question: str
    intent: NotRequired[dict[str, object]]
    next_expert: NotRequired[ExpertName]
    task_plan: NotRequired[list[TaskName]]
    plan_source: NotRequired[str]
    metric_metadata: NotRequired[str | None]
    query_evidence: NotRequired[dict[str, object] | None]
    answer: NotRequired[str]
    session_context: NotRequired[str]


class WorkflowResult(BaseModel):
    intent: IntentResult
    next_expert: ExpertName
    answer: str | None = None
    run_id: str | None = None


def build_workflow(
    model: ChatModel, reference_date: date,
    metric_catalog: Sequence[MetricDefinition] | None = None,
    database_path: Path = DEFAULT_DB_PATH,
    query_model: ToolCallingModel | None = None,
    analysis_model: ChatModel | None = None,
    checkpointer: object | None = None,
    ops_path: Path = DEFAULT_OPS_PATH,
    harness: RunHarness | None = None,
):
    """Compile Intent → Supervisor → Query → optional Analysis once."""

    catalog = tuple(metric_catalog) if metric_catalog is not None else load_metric_catalog(CATALOG_PATH)
    knowledge = load_knowledge(catalog)
    runtime = harness or RunHarness()

    def intent_node(state: WorkflowState) -> dict[str, dict[str, object]]:
        result = runtime.call("intent", "recognize_intent", lambda: run_intent_once(
            state["question"], model, reference_date, state.get("session_context")))
        return {"intent": result.model_dump(mode="json")}

    def supervisor_node(state: WorkflowState) -> dict[str, object]:
        intent = IntentResult.model_validate(state["intent"])
        tasks, source = runtime.call("supervisor", "plan_tasks", lambda: plan_with_model(
            intent, state["question"], model))
        if tasks is None:
            return {
                "next_expert": select_expert(intent), "task_plan": [], "plan_source": source,
                "answer": "当前尚不支持这组复合任务；请先分别提问。",
            }
        return {"next_expert": select_expert(intent, tasks),
                "task_plan": list(tasks), "plan_source": source}

    def other_node(state: WorkflowState) -> dict[str, str]:
        return {"answer": runtime.call("other", "answer_other",
                                       lambda: answer_other(state["question"]))}

    def ops_node(state: WorkflowState) -> dict[str, str]:
        return {"answer": runtime.call("ops", "read_runs",
                                       lambda: answer_ops(state["question"], ops_path))}

    def query_node(state: WorkflowState) -> dict[str, object]:
        intent = IntentResult.model_validate(state["intent"])
        tasks = state["task_plan"]
        metadata_parts = []
        if "table_meta" in tasks:
            metadata_parts.append(answer_table_meta(intent, catalog, database_path, knowledge))
        if "lineage" in tasks:
            metric = next((item for item in catalog if item.name == intent.metric_name), None)
            if metric is None:
                metadata_parts.append("指标未在目录中批准，无法追溯血缘。")
            else:
                try:
                    metadata_parts.append(describe_lineage(metric, database_path, knowledge))
                except ValueError:
                    metadata_parts.append("指标来源未通过核对，无法追溯血缘。")
        metadata = "\n".join(metadata_parts) if metadata_parts else None
        if "data_query" not in tasks:
            return {"answer": metadata, "metric_metadata": metadata}
        query_model_for_task = query_model or model
        if not hasattr(query_model_for_task, "bind_tools"):
            return {"answer": "取数模型不支持工具调用；没有执行查询。", "query_evidence": None}
        result = runtime.call("query", "answer_query", lambda: fetch_query_evidence(
            intent, state["question"], catalog, database_path, query_model_for_task,
            knowledge, runtime))
        if isinstance(result, str):
            return {"answer": result, "query_evidence": None}
        if state["next_expert"] == "analysis":
            return {"query_evidence": result.model_dump(mode="json"),
                    "metric_metadata": metadata}
        answer = answer_query(result)
        if metadata:
            answer = f"指标口径：{metadata}\n查询结果：{answer}"
        return {"answer": answer}

    def analysis_node(state: WorkflowState) -> dict[str, str]:
        evidence = QueryEvidence.model_validate(state["query_evidence"])
        answer = runtime.call("analysis", "analyze", lambda: answer_analysis_question(
            state["question"], evidence, analysis_model or model))
        if state.get("metric_metadata"):
            answer = f"指标口径：{state['metric_metadata']}\n分析结果：{answer}"
        return {"answer": answer}

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
    graph.add_node("ops", ops_node)
    graph.add_edge(START, "intent")
    graph.add_edge("intent", "supervisor")
    graph.add_conditional_edges(
        "supervisor", route_after_supervisor,
        {"other": "other", "ops": "ops", "query": "query", "analysis": "query", "done": END},
    )
    graph.add_conditional_edges("query", route_after_query, {"analysis": "analysis", "done": END})
    graph.add_edge("analysis", END)
    graph.add_edge("other", END)
    return graph.compile(checkpointer=checkpointer)


def run_workflow(
    question: str, model: ChatModel, reference_date: date,
    metric_catalog: Sequence[MetricDefinition] | None = None,
    database_path: Path = DEFAULT_DB_PATH,
    query_model: ToolCallingModel | None = None,
    analysis_model: ChatModel | None = None,
    session_id: str | None = None,
    checkpoint_path: Path | None = None,
    ops_path: Path = DEFAULT_OPS_PATH,
    record_path: Path | None = Path("data/ops/execution_events.jsonl"),
    timeout_seconds: float = 120,
) -> WorkflowResult:
    if not question.strip():
        raise ValueError("Question must not be empty")
    runtime = RunHarness(record_path, timeout_seconds=timeout_seconds)
    runtime.record("workflow", "run", "started")
    try:
        state = _invoke_workflow(
            question, model, reference_date, metric_catalog, database_path, query_model,
            analysis_model, session_id, checkpoint_path, ops_path, runtime)
    except Exception as exc:
        runtime.record("workflow", "run", "failed", error_type=type(exc).__name__)
        raise
    runtime.record("workflow", "run", "completed")
    return WorkflowResult(
        intent=state["intent"], next_expert=state["next_expert"],
        answer=state.get("answer"), run_id=runtime.run_id,
    )


def _invoke_workflow(
    question: str, model: ChatModel, reference_date: date,
    metric_catalog: Sequence[MetricDefinition] | None, database_path: Path,
    query_model: ToolCallingModel | None, analysis_model: ChatModel | None,
    session_id: str | None, checkpoint_path: Path | None, ops_path: Path,
    runtime: RunHarness,
) -> WorkflowState:
    if session_id is not None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", session_id):
            raise ValueError("会话 ID 只能包含字母、数字、下划线和短横线，最长 64 位")
        if checkpoint_path is None:
            raise ValueError("持久会话需要指定 checkpoint_path")
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        with SqliteSaver.from_conn_string(str(checkpoint_path)) as saver:
            graph = build_workflow(model, reference_date, metric_catalog, database_path,
                                   query_model, analysis_model, checkpointer=saver,
                                   ops_path=ops_path, harness=runtime)
            config = {"configurable": {"thread_id": session_id}}
            state = graph.invoke({"question": question.strip()}, config)
            current = IntentResult.model_validate(state["intent"])
            context = json.dumps({
                "metric_name": current.metric_name,
                "metric_names": current.metric_names,
                "dates": [day.isoformat() for day in current.requested_dates()],
                "dimensions": current.dimensions,
                "scope": current.scope,
            }, ensure_ascii=False)
            graph.update_state(config, {"session_context": context})
    else:
        state = build_workflow(
            model, reference_date, metric_catalog, database_path, query_model, analysis_model,
            ops_path=ops_path, harness=runtime,
        ).invoke({"question": question.strip()})
    return state


def main() -> None:
    parser = argparse.ArgumentParser(description="电商分析 Agent：单一合成数据源与统一取数链")
    parser.add_argument("question", help="输入问题")
    parser.add_argument("--session-id", help="同一 ID 的问题可使用上一轮已确认信息")
    parser.add_argument("--checkpoint-db", type=Path,
                        default=Path("data/sessions/checkpoints.sqlite"))
    parser.add_argument("--ops-db", type=Path, default=DEFAULT_OPS_PATH)
    parser.add_argument("--record-path", type=Path,
                        default=Path("data/ops/execution_events.jsonl"))
    parser.add_argument("--timeout-seconds", type=float, default=120)
    args = parser.parse_args()
    if os.environ.get("TAOBAO_UCI_DB_PATH") or os.environ.get("TAOBAO_DEMO_DATA"):
        parser.exit(2, "旧数据源开关已移除；请取消 TAOBAO_UCI_DB_PATH / TAOBAO_DEMO_DATA。\n")
    database_path = Path(os.environ.get("TAOBAO_DB_PATH", str(DEFAULT_DB_PATH)))
    try:
        model = create_qwen_model()
        catalog = load_metric_catalog(CATALOG_PATH)
        result = run_workflow(
            args.question, model, date.today(), catalog, database_path,
            query_model=model, analysis_model=model,
            session_id=args.session_id, checkpoint_path=args.checkpoint_db if args.session_id else None,
            ops_path=args.ops_db,
            record_path=args.record_path, timeout_seconds=args.timeout_seconds,
        )
    except ModelRateLimitError:
        parser.exit(3, "百炼返回 429：当前请求受限流，本次任务未完成。\n")
    except (ValueError, RuntimeError, OSError, sqlite3.Error, ToolUnavailableError) as exc:
        parser.exit(2, f"运行失败：{exc}\n")
    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False))
