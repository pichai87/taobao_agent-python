"""FastAPI request/response and SSE adapters over the same workflow."""

import os
import sqlite3
from datetime import date
from pathlib import Path
from queue import Queue
from threading import Thread
from typing import Callable

from fastapi import FastAPI, HTTPException
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, ConfigDict, Field

from taobao_agent.commerce_data import DEFAULT_DB_PATH
from taobao_agent.agents.harness import ToolUnavailableError
from taobao_agent.model import ChatModel, create_qwen_model
from taobao_agent.workflow import WorkflowResult, run_workflow


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=2000)
    session_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")


def create_app(
    model_factory: Callable[[], ChatModel] = create_qwen_model,
    *, database_path: Path | None = None,
    checkpoint_path: Path = Path("data/sessions/checkpoints.sqlite"),
    record_path: Path = Path("data/ops/execution_events.jsonl"),
    reference_date: date | None = None,
) -> FastAPI:
    app = FastAPI(title="百亿补贴数据分析助手", version="0.1.0")

    def execute(request: AskRequest, event_sink=None) -> WorkflowResult:
        return run_workflow(
            request.question, model_factory(), reference_date or date.today(),
            database_path=database_path or Path(os.environ.get("TAOBAO_DB_PATH", str(DEFAULT_DB_PATH))),
            session_id=request.session_id,
            checkpoint_path=checkpoint_path if request.session_id else None,
            record_path=record_path,
            event_sink=event_sink,
        )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/ask", response_model=WorkflowResult)
    def ask(request: AskRequest) -> WorkflowResult:
        try:
            return execute(request)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (RuntimeError, OSError, sqlite3.Error, ToolUnavailableError) as exc:
            raise HTTPException(status_code=503, detail="工作流暂时不可用，请检查本地服务日志") from exc

    @app.post("/v1/ask/stream", response_class=EventSourceResponse)
    def stream(request: AskRequest):
        events: Queue[tuple[str, object]] = Queue()

        def worker() -> None:
            try:
                result = execute(request, lambda item: events.put(("step", item)))
                events.put(("result", result.model_dump(mode="json")))
            except Exception as exc:
                events.put(("error", {"type": type(exc).__name__,
                                       "message": "执行失败；请检查服务日志或普通接口的错误信息。"}))
            finally:
                events.put(("done", {}))

        Thread(target=worker, daemon=True).start()
        while True:
            kind, data = events.get()
            yield ServerSentEvent(data=data, event=kind)
            if kind == "done":
                break

    return app


app = create_app()
