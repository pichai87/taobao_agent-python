"""Read-only local operations tools with parameterized lookups."""

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RunRecord:
    id: int
    job_name: str
    status: str
    started_at: str
    finished_at: str | None
    summary: str


def read_run(path: Path, run_id: int | None = None) -> RunRecord | None:
    uri = f"{path.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        connection.execute("PRAGMA query_only = ON")
        sql = ("SELECT id,job_name,status,started_at,finished_at,summary FROM runs "
               + ("ORDER BY id DESC LIMIT 1" if run_id is None else "WHERE id=?"))
        row = connection.execute(sql, () if run_id is None else (run_id,)).fetchone()
    return RunRecord(*row) if row else None


def read_run_logs(path: Path, run_id: int) -> tuple[str, ...]:
    uri = f"{path.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        connection.execute("PRAGMA query_only = ON")
        rows = connection.execute(
            "SELECT message FROM logs WHERE run_id=? ORDER BY step LIMIT 100", (run_id,)
        ).fetchall()
    return tuple(row[0] for row in rows)
