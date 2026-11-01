"""Read recorded local operations, never invent remote DataWorks status."""

import re
import sqlite3
from pathlib import Path

from taobao_agent.agents.harness import RunHarness
from taobao_agent.ops_store import DEFAULT_OPS_PATH
from taobao_agent.tools.ops import read_run, read_run_logs


def answer_ops(question: str, ops_path: Path = DEFAULT_OPS_PATH,
               harness: RunHarness | None = None) -> str:
    """Answer the latest or explicitly numbered local run using stored evidence."""

    if not ops_path.is_file():
        return "尚无本地数据质量检查记录；先运行 taobao-check-data。"
    match = re.search(r"(?:#|run[ _-]?id[=: ]+|运行编号[：: ]*)(\d+)", question, re.I)
    run_id = int(match.group(1)) if match else None
    try:
        row = (harness.call("ops", "read_runs", lambda: read_run(ops_path, run_id))
               if harness else read_run(ops_path, run_id))
        if row is None:
            return "没有找到对应的本地任务运行记录。"
        logs = (harness.call("ops", "read_run_logs", lambda: read_run_logs(ops_path, row.id))
                if harness else read_run_logs(ops_path, row.id))
    except (OSError, ValueError, sqlite3.Error):
        return "本地任务记录不可读取，未返回未经核实的状态。"
    return (f"本地任务 #{row.id}（{row.job_name}）状态：{row.status}；开始：{row.started_at}；"
            f"结束：{row.finished_at or '尚未结束'}；结论：{row.summary}。日志："
            + " | ".join(logs))
