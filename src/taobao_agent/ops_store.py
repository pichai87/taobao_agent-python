"""Durable records for the local, actually executed commerce quality job."""

import argparse
import json
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from taobao_agent.commerce_data import CATALOG_PATH, DEFAULT_DB_PATH, inspect_metric, query_metric
from taobao_agent.metric_catalog import load_metric_catalog


DEFAULT_OPS_PATH = Path("data/ops/runs.sqlite")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_quality_run(database_path: Path = DEFAULT_DB_PATH,
                       ops_path: Path = DEFAULT_OPS_PATH) -> int:
    """Run a real read-only check, then record its result and diagnostic messages."""

    ops_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(ops_path)) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS runs ("
                           "id INTEGER PRIMARY KEY, job_name TEXT NOT NULL, status TEXT NOT NULL, "
                           "started_at TEXT NOT NULL, finished_at TEXT, summary TEXT NOT NULL)")
        connection.execute("CREATE TABLE IF NOT EXISTS logs ("
                           "run_id INTEGER NOT NULL, step INTEGER NOT NULL, message TEXT NOT NULL, "
                           "PRIMARY KEY (run_id, step), FOREIGN KEY (run_id) REFERENCES runs(id))")
        stale_before = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        stale = connection.execute(
            "SELECT id FROM runs WHERE status='running' AND started_at < ?", (stale_before,)
        ).fetchall()
        for (old_id,) in stale:
            connection.execute(
                "UPDATE runs SET status='failed', finished_at=?, summary=? WHERE id=?",
                (_utc_now(), "上次进程中断，超过 5 分钟未完成", old_id),
            )
            next_step = connection.execute(
                "SELECT COALESCE(MAX(step),0)+1 FROM logs WHERE run_id=?", (old_id,)
            ).fetchone()[0]
            connection.execute("INSERT INTO logs VALUES (?,?,?)",
                               (old_id, next_step, "恢复检查：旧运行超过 5 分钟，标记为 failed"))
        cursor = connection.execute(
            "INSERT INTO runs(job_name,status,started_at,summary) VALUES (?,?,?,?)",
            ("commerce_quality", "running", _utc_now(), "数据质量检查运行中"),
        )
        run_id = cursor.lastrowid
        connection.commit()
        logs = ["开始核对合成数据来源、指标字段和可执行查询"]
        try:
            catalog = load_metric_catalog(CATALOG_PATH)
            for metric in catalog:
                first, last, fields = inspect_metric(metric, database_path)
                result = query_metric(metric, date.fromisoformat(first),
                                      date.fromisoformat(last), database_path)
                logs.append(f"{metric.name}：字段 {','.join(fields)} 已核对；"
                            f"{first} 至 {last} 汇总={result.total} {result.unit}")
            status, summary = "success", f"{len(catalog)} 项指标通过来源、字段和查询核对"
        except (OSError, ValueError, sqlite3.Error) as exc:
            status, summary = "failed", f"数据质量检查失败：{type(exc).__name__}: {exc}"
            logs.append(summary)
        connection.executemany("INSERT INTO logs VALUES (?,?,?)",
                               [(run_id, index, message) for index, message in enumerate(logs, 1)])
        connection.execute("UPDATE runs SET status=?, finished_at=?, summary=? WHERE id=?",
                           (status, _utc_now(), summary, run_id))
        connection.commit()
        return run_id


def main() -> None:
    parser = argparse.ArgumentParser(description="执行并记录本地电商数据质量检查")
    parser.add_argument("--database", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--ops-db", type=Path, default=DEFAULT_OPS_PATH)
    args = parser.parse_args()
    run_id = record_quality_run(args.database, args.ops_db)
    print(json.dumps({"run_id": run_id, "ops_database": str(args.ops_db)}, ensure_ascii=False))
