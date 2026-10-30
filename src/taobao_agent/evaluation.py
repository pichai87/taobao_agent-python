"""A small, reproducible business evaluation for the current UCI workflow."""

import argparse
import json
import os
from datetime import date
from pathlib import Path
from time import perf_counter

from pydantic import BaseModel, ConfigDict, Field, model_validator

from taobao_agent.intent_agent import IntentType
from taobao_agent.metric_catalog import load_metric_catalog
from taobao_agent.model import ToolCallingModel, create_qwen_model
from taobao_agent.supervisor import ExpertName
from taobao_agent.uci_online_retail import UCI_CATALOG_PATH
from taobao_agent.workflow import WorkflowResult, run_workflow


DEFAULT_DATASET_PATH = Path(__file__).resolve().parents[2] / "scripts/eval/datasets/default.json"


class GoldenCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    expected_intents: tuple[IntentType, ...] = Field(min_length=1, max_length=2)
    expected_expert: ExpertName
    expected_metric: str | None
    expected_dates: tuple[date, ...]
    expected_dimensions: tuple[str, ...] = ()
    required_answer: tuple[str, ...] = ()
    forbidden_answer: tuple[str, ...] = ()

    @model_validator(mode="after")
    def check_expectations(self) -> "GoldenCase":
        if len(set(self.expected_intents)) != len(self.expected_intents):
            raise ValueError("expected_intents must be distinct")
        if not self.required_answer and not self.forbidden_answer:
            raise ValueError("at least one answer assertion is required")
        return self


class GoldenDataset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reference_date: date
    cases: tuple[GoldenCase, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def check_case_ids(self) -> "GoldenDataset":
        ids = [case.id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("golden case ids must be unique")
        return self


class CaseOutcome(BaseModel):
    case_id: str
    passed: bool
    checks: dict[str, bool]
    elapsed_ms: int
    error_type: str | None = None


class EvaluationReport(BaseModel):
    total: int
    passed: int
    cases: tuple[CaseOutcome, ...]


def load_dataset(path: Path = DEFAULT_DATASET_PATH) -> GoldenDataset:
    return GoldenDataset.model_validate_json(path.read_text(encoding="utf-8"))


def score_case(case: GoldenCase, result: WorkflowResult, elapsed_ms: int = 0) -> CaseOutcome:
    """Score exact, observable behavior; do not use a model as a judge."""

    answer = result.answer or ""
    actual_intents = {result.intent.intent_type, *result.intent.secondary_intents}
    checks = {
        "intents": actual_intents == set(case.expected_intents),
        "expert": result.next_expert == case.expected_expert,
        "metric": result.intent.metric_name == case.expected_metric,
        "dates": result.intent.requested_dates() == case.expected_dates,
        "dimensions": tuple(result.intent.dimensions) == case.expected_dimensions,
        "required_answer": all(phrase in answer for phrase in case.required_answer),
        "forbidden_answer": all(phrase not in answer for phrase in case.forbidden_answer),
    }
    return CaseOutcome(
        case_id=case.id, passed=all(checks.values()), checks=checks,
        elapsed_ms=elapsed_ms,
    )


def evaluate_dataset(
    dataset: GoldenDataset, database_path: Path, model: ToolCallingModel,
) -> EvaluationReport:
    """Run each question independently and keep failures visible in the report."""

    catalog = load_metric_catalog(UCI_CATALOG_PATH)
    outcomes: list[CaseOutcome] = []
    for case in dataset.cases:
        started = perf_counter()
        try:
            result = run_workflow(
                case.question, model, dataset.reference_date, catalog,
                uci_db_path=database_path, query_model=model, analysis_model=model,
            )
            elapsed_ms = round((perf_counter() - started) * 1000)
            outcomes.append(score_case(case, result, elapsed_ms))
        except Exception as exc:
            outcomes.append(CaseOutcome(
                case_id=case.id, passed=False, checks={},
                elapsed_ms=round((perf_counter() - started) * 1000),
                error_type=type(exc).__name__,
            ))
    return EvaluationReport(
        total=len(outcomes), passed=sum(outcome.passed for outcome in outcomes),
        cases=tuple(outcomes),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="用固定业务题集评测当前 UCI Agent")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET_PATH)
    parser.add_argument("--database", type=Path, default=None)
    args = parser.parse_args()
    database_path = args.database or Path(os.environ.get("TAOBAO_UCI_DB_PATH", ""))
    if not database_path.is_file():
        parser.exit(2, "请用 --database 或 TAOBAO_UCI_DB_PATH 指定已导入的 UCI SQLite 文件。\n")
    try:
        dataset = load_dataset(args.dataset)
        model = create_qwen_model()
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(2, f"评测准备失败：{exc}\n")
    report = evaluate_dataset(dataset, database_path, model)
    print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False))
    if report.passed != report.total:
        raise SystemExit(1)
