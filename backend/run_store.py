"""
Persistent storage for workflow (engagement) runs.

This is its own SQLite database (data/runs.sqlite3), deliberately separate
from backend/memory.py (LLM-context memory), backend/knowledge_base.py (RAG
documents) and backend/audit.py (append-only governance log). It exists so
the operator UI's workflow history, dashboard and metrics survive a restart.

What is stored per run: the request id, task metadata (objective, priority,
department, submitter), status, timestamps, duration, current stage,
progress, the agents the AI Executive selected, the failure message if any,
the final workflow result, and the remaining run detail (stage table, event
feed, drafts, review passes, per-agent executions) as JSON.

Unlike the audit log, runs are mutable while they execute, so this store
upserts. Fails safe like the other stores: if the database can't be opened,
save() returns False and load_all() returns [] — the API keeps working from
memory for the life of the process instead of crashing.
"""
import json
import sqlite3
import warnings
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "runs.sqlite3"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    request_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    user_request TEXT NOT NULL,
    task TEXT NOT NULL DEFAULT '',
    priority TEXT NOT NULL DEFAULT 'normal',
    department TEXT NOT NULL DEFAULT '',
    submitted_by TEXT NOT NULL DEFAULT '',
    request_type TEXT NOT NULL DEFAULT '',
    engine TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    updated_at TEXT NOT NULL,
    duration_ms INTEGER,
    current_stage TEXT NOT NULL DEFAULT '',
    progress REAL NOT NULL DEFAULT 0,
    selected_agents TEXT NOT NULL DEFAULT '[]',
    error TEXT NOT NULL DEFAULT '',
    result TEXT NOT NULL DEFAULT '{}',
    data TEXT NOT NULL DEFAULT '{}'
)
"""

# Columns mirrored from the run dict (also kept in `data`; the column is authoritative on load).
_MIRRORED = (
    "status", "user_request", "priority", "department", "submitted_by", "request_type",
    "engine", "created_at", "started_at", "completed_at", "duration_ms", "current_stage", "error",
)


def _json(value) -> str:
    return json.dumps(value, default=str)


class RunStore:
    """SQLite-backed, upserting store of workflow runs. One short-lived
    connection per operation, always closed."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
        self._unavailable = False
        self._init_db()

    @property
    def is_available(self) -> bool:
        return not self._unavailable

    def _init_db(self) -> None:
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            with self._connection() as conn:
                conn.execute(_SCHEMA)
        except (sqlite3.Error, OSError) as exc:
            self._unavailable = True
            warnings.warn(
                f"RunStore: database unavailable at {self.db_path} ({exc}); "
                "workflow runs will not be persisted this session.",
                RuntimeWarning,
            )

    @contextmanager
    def _connection(self):
        conn = sqlite3.connect(str(self.db_path), timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def save(self, run: dict) -> bool:
        """Insert or update one run. Keys starting with "_" are process-local
        working state and are never persisted, except "_task" (stored as the
        `task` column). Returns False (no-op) if unavailable or the write fails."""
        if self._unavailable:
            return False
        try:
            data = {k: v for k, v in run.items() if not k.startswith("_") and k != "state"}
            agents = [t.get("agent_id") for t in run.get("tasks", []) if t.get("agent_id")]
            with self._connection() as conn:
                conn.execute(
                    "INSERT INTO runs (request_id, status, user_request, task, priority, department, "
                    "submitted_by, request_type, engine, created_at, started_at, completed_at, updated_at, "
                    "duration_ms, current_stage, progress, selected_agents, error, result, data) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(request_id) DO UPDATE SET "
                    "status=excluded.status, user_request=excluded.user_request, task=excluded.task, "
                    "priority=excluded.priority, department=excluded.department, "
                    "submitted_by=excluded.submitted_by, request_type=excluded.request_type, "
                    "engine=excluded.engine, started_at=excluded.started_at, "
                    "completed_at=excluded.completed_at, updated_at=excluded.updated_at, "
                    "duration_ms=excluded.duration_ms, current_stage=excluded.current_stage, "
                    "progress=excluded.progress, selected_agents=excluded.selected_agents, "
                    "error=excluded.error, result=excluded.result, data=excluded.data",
                    (
                        run["request_id"], run["status"], run["user_request"], run.get("_task", ""),
                        run.get("priority", "normal"), run.get("department", ""),
                        run.get("submitted_by", ""), run.get("request_type", ""), run.get("engine", ""),
                        run["created_at"], run.get("started_at"), run.get("completed_at"),
                        datetime.now(timezone.utc).isoformat(), run.get("duration_ms"),
                        run.get("current_stage", ""), float(run.get("progress", 0.0)),
                        _json(agents), run.get("error", ""), _json(run.get("state") or {}), _json(data),
                    ),
                )
            return True
        except (sqlite3.Error, TypeError, ValueError, KeyError) as exc:
            warnings.warn(f"RunStore.save failed: {exc}", RuntimeWarning)
            return False

    @staticmethod
    def _row_to_run(row: sqlite3.Row) -> dict:
        run = json.loads(row["data"]) if row["data"] else {}
        for column in _MIRRORED:
            run[column] = row[column]
        run["request_id"] = row["request_id"]
        run["progress"] = row["progress"]
        run["state"] = json.loads(row["result"]) if row["result"] else {}
        run["_task"] = row["task"]
        run["_t0"] = None
        run["_stage_t0"] = {}
        return run

    def load_all(self) -> list:
        """Every stored run, oldest first (insertion order). [] if unavailable."""
        if self._unavailable:
            return []
        try:
            with self._connection() as conn:
                rows = conn.execute("SELECT * FROM runs ORDER BY rowid").fetchall()
            return [self._row_to_run(row) for row in rows]
        except (sqlite3.Error, ValueError) as exc:
            warnings.warn(f"RunStore.load_all failed: {exc}", RuntimeWarning)
            return []

    def get(self, request_id: str) -> Optional[dict]:
        if self._unavailable:
            return None
        try:
            with self._connection() as conn:
                row = conn.execute("SELECT * FROM runs WHERE request_id = ?", (request_id,)).fetchone()
            return self._row_to_run(row) if row else None
        except (sqlite3.Error, ValueError) as exc:
            warnings.warn(f"RunStore.get failed: {exc}", RuntimeWarning)
            return None

    def __len__(self) -> int:
        if self._unavailable:
            return 0
        try:
            with self._connection() as conn:
                return conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        except sqlite3.Error:
            return 0


default_run_store = RunStore()
