"""Runtime permissions, bounded retries and privacy-minimal execution records."""

import json
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Sequence
from uuid import uuid4

from taobao_agent.metric_search import MetricDefinition, search_metrics


class ToolUnavailableError(Exception):
    """A tool timed out or lost its connection."""


class HarnessError(RuntimeError):
    """A call exceeded its approved permission or budget."""


_ALLOWED = {
    "intent": frozenset({"recognize_intent"}),
    "supervisor": frozenset({"plan_tasks"}),
    "query": frozenset({"answer_query", "choose_tool", "search_metrics", "rule_search",
                         "cap_search", "snippet_search", "build_sql", "execute_query"}),
    "analysis": frozenset({"analyze"}),
    "ops": frozenset({"read_runs"}),
    "other": frozenset({"answer_other"}),
}


class RunHarness:
    """A per-question guard. Records names/status only, never prompts or tool arguments."""

    def __init__(self, record_path: Path | None = None, *, timeout_seconds: float = 120,
                 max_calls: int = 64) -> None:
        if timeout_seconds <= 0 or max_calls < 1:
            raise ValueError("Harness 时间与调用次数必须为正数")
        self.run_id = uuid4().hex
        self.record_path = record_path
        self.deadline = monotonic() + timeout_seconds
        self.max_calls = max_calls
        self.calls = 0
        self.events: list[dict[str, object]] = []

    def record(self, expert: str, action: str, status: str, elapsed_ms: int = 0,
                error_type: str | None = None) -> None:
        event = {
            "run_id": self.run_id, "at": datetime.now(timezone.utc).isoformat(),
            "expert": expert, "action": action, "status": status,
            "elapsed_ms": elapsed_ms, "error_type": error_type,
        }
        self.events.append(event)
        if self.record_path is not None:
            self.record_path.parent.mkdir(parents=True, exist_ok=True)
            with self.record_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event, ensure_ascii=False) + "\n")

    def call(self, expert: str, action: str, operation):
        """Allowlist, total deadline and one retry for transient connection failures."""

        if action not in _ALLOWED.get(expert, frozenset()):
            self.record(expert, action, "denied", error_type="PermissionDenied")
            raise HarnessError(f"{expert} 无权调用 {action}")
        for attempt in range(2):
            if self.calls >= self.max_calls or monotonic() >= self.deadline:
                self.record(expert, action, "budget_exceeded", error_type="BudgetExceeded")
                raise HarnessError("执行次数或总时限已达到上限")
            self.calls += 1
            started = monotonic()
            try:
                result = operation()
                if monotonic() >= self.deadline:
                    raise TimeoutError("调用结束时已超过总时限")
                self.record(expert, action, "success", round((monotonic()-started)*1000))
                return result
            except (TimeoutError, ConnectionError, ToolUnavailableError) as exc:
                self.record(expert, action, "retry" if attempt == 0 else "failed",
                             round((monotonic()-started)*1000), type(exc).__name__)
                if attempt:
                    raise ToolUnavailableError(f"{action} 经重试仍不可用") from exc
            except Exception as exc:
                self.record(expert, action, "failed", round((monotonic()-started)*1000),
                             type(exc).__name__)
                raise


def guarded_metric_search(
    name: str, catalog: Sequence[MetricDefinition]
) -> MetricDefinition | None:
    """Keep operational lookup failures separate from a missing metric."""

    try:
        return search_metrics(name, catalog)
    except (TimeoutError, ConnectionError) as exc:
        raise ToolUnavailableError("metric search unavailable") from exc
