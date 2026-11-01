"""Bounded, session-isolated conversation history; never a source of numeric facts."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE IF NOT EXISTS conversation_turns ("
        "id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, question TEXT NOT NULL, "
        "answer TEXT NOT NULL, slots_json TEXT NOT NULL)"
    )
    connection.execute("CREATE INDEX IF NOT EXISTS turn_session_idx "
                       "ON conversation_turns(session_id, id)")
    return connection


def remember(path: Path, session_id: str, question: str, answer: str,
             slots: dict[str, object]) -> None:
    """Persist only completed answers, with compact verified intent slots."""

    if not answer or len(question) > 2000 or len(answer) > 6000:
        return
    with closing(_connect(path)) as connection:
        connection.execute(
            "INSERT INTO conversation_turns(session_id, question, answer, slots_json) "
            "VALUES (?, ?, ?, ?)",
            (session_id, question, answer, json.dumps(slots, ensure_ascii=False)),
        )
        connection.commit()


def recall(path: Path, session_id: str, question: str, *, limit: int = 3,
           char_budget: int = 1800) -> str:
    """Use recent turns plus one older metric match; trim text before the prompt."""

    if not path.is_file():
        return ""
    with closing(_connect(path)) as connection:
        rows = connection.execute(
            "SELECT question,answer,slots_json FROM conversation_turns "
            "WHERE session_id=? ORDER BY id DESC LIMIT 30", (session_id,),
        ).fetchall()
    selected = rows[:limit]
    for item in rows[limit:]:
        try:
            slots = json.loads(item[2])
            names = [slots.get("metric_name"), *(slots.get("metric_names") or [])]
            if any(name and str(name).lower() in question.lower() for name in names):
                selected.append(item)
                break
        except (TypeError, ValueError):
            continue
    lines = []
    for prior_question, prior_answer, slots_json in selected:
        excerpt = f"用户：{prior_question[:180]}；助手：{prior_answer[:300]}；已核实槽位：{slots_json[:300]}"
        if len("\n".join(lines)) + len(excerpt) > char_budget:
            break
        lines.append(excerpt)
    return "\n".join(lines)
