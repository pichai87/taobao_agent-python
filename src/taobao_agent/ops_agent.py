"""Read recorded local operations, never invent remote DataWorks status."""

import re
import sqlite3
from contextlib import closing
from pathlib import Path

from taobao_agent.ops_store import DEFAULT_OPS_PATH


def answer_ops(question: str, ops_path: Path = DEFAULT_OPS_PATH) -> str:
    """Answer the latest or explicitly numbered local run using stored evidence."""

    if not ops_path.is_file():
        return "尚无本地数据质量检查记录；先运行 taobao-check-data。"
    match = re.search(r"(?:#|run[ _-]?id[=: ]+|运行编号[：: ]*)(\d+)", question, re.I)
    run_id = int(match.group(1)) if match else None
    uri = f"{ops_path.resolve().as_uri()}?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            connection.execute("PRAGMA query_only = ON")
            if run_id is None:
                row = connection.execute(
                    "SELECT id,job_name,status,started_at,finished_at,summary "
                    "FROM runs ORDER BY id DESC LIMIT 1"
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT id,job_name,status,started_at,finished_at,summary "
                    "FROM runs WHERE id=?", (run_id,)
                ).fetchone()
            if row is None:
                return "没有找到对应的本地任务运行记录。"
            logs = connection.execute("SELECT message FROM logs WHERE run_id=? ORDER BY step",
                                      (row[0],)).fetchall()
    except sqlite3.Error:
        return "本地任务记录不可读取，未返回未经核实的状态。"
    return (f"本地任务 #{row[0]}（{row[1]}）状态：{row[2]}；开始：{row[3]}；"
            f"结束：{row[4] or '尚未结束'}；结论：{row[5]}。日志："
            + " | ".join(item[0] for item in logs))
