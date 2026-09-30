"""
Persistent Memory layer for the AI Workforce.

Stores durable, cross-execution task/content records in a local SQLite
database (stdlib sqlite3 — no new dependency), completely separate from
backend/state.py's WorkflowState (which is per-request, in-memory, and
scoped to a single LangGraph run). Search is deterministic keyword-overlap
scoring, not vector embeddings — that's explicitly out of scope for now.

Fails safe: if the database file/directory can't be opened (bad path,
permissions, disk issues), MemoryStore flips into a degraded mode where
store()/search()/get()/delete() all return harmless empty/no-op results
instead of raising, so a broken memory backend never takes down a caller
like AIExecutive.
"""
import json
import re
import sqlite3
import uuid
import warnings
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "memory.sqlite3"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory (
    id TEXT PRIMARY KEY,
    task TEXT NOT NULL,
    content TEXT NOT NULL,
    agent_id TEXT,
    created_at TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}'
)
"""

_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> set:
    return set(_WORD_RE.findall((text or "").lower()))


@dataclass(frozen=True)
class MemoryRecord:
    id: str
    task: str
    content: str
    agent_id: Optional[str]
    created_at: str  # ISO 8601, UTC
    metadata: dict = field(default_factory=dict)


def _row_to_record(row: sqlite3.Row) -> MemoryRecord:
    return MemoryRecord(
        id=row["id"],
        task=row["task"],
        content=row["content"],
        agent_id=row["agent_id"],
        created_at=row["created_at"],
        metadata=json.loads(row["metadata"]) if row["metadata"] else {},
    )


class MemoryStore:
    """SQLite-backed persistent memory. One connection per operation (short-lived,
    always closed) so concurrent callers — e.g. a background workflow thread —
    don't share a single non-thread-safe sqlite3 connection."""

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
                f"MemoryStore: database unavailable at {self.db_path} ({exc}); "
                "memory will be a no-op for this session.",
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

    def store(
        self,
        task: str,
        content: str,
        *,
        agent_id: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> Optional[MemoryRecord]:
        """Persist a new memory record. Returns None (no-op) if the store is unavailable."""
        if self._unavailable:
            return None
        record = MemoryRecord(
            id=str(uuid.uuid4()),
            task=task,
            content=content,
            agent_id=agent_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            metadata=dict(metadata or {}),
        )
        try:
            with self._connection() as conn:
                conn.execute(
                    "INSERT INTO memory (id, task, content, agent_id, created_at, metadata) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        record.id, record.task, record.content, record.agent_id,
                        record.created_at, json.dumps(record.metadata),
                    ),
                )
        except sqlite3.Error as exc:
            warnings.warn(f"MemoryStore.store failed: {exc}", RuntimeWarning)
            return None
        return record

    def get(self, memory_id: str) -> Optional[MemoryRecord]:
        """Look up a single record by id. Returns None if missing or unavailable."""
        if self._unavailable:
            return None
        try:
            with self._connection() as conn:
                row = conn.execute("SELECT * FROM memory WHERE id = ?", (memory_id,)).fetchone()
        except sqlite3.Error as exc:
            warnings.warn(f"MemoryStore.get failed: {exc}", RuntimeWarning)
            return None
        return _row_to_record(row) if row else None

    def count(self) -> int:
        """Total stored records. Returns 0 if the store is unavailable."""
        if self._unavailable:
            return 0
        try:
            with self._connection() as conn:
                return conn.execute("SELECT COUNT(*) FROM memory").fetchone()[0]
        except sqlite3.Error as exc:
            warnings.warn(f"MemoryStore.count failed: {exc}", RuntimeWarning)
            return 0

    def list_recent(self, limit: int = 100) -> list:
        """Newest-first listing (read-only). Returns [] if unavailable."""
        if self._unavailable:
            return []
        try:
            with self._connection() as conn:
                rows = conn.execute(
                    "SELECT * FROM memory ORDER BY rowid DESC LIMIT ?", (limit,)
                ).fetchall()
        except sqlite3.Error as exc:
            warnings.warn(f"MemoryStore.list_recent failed: {exc}", RuntimeWarning)
            return []
        return [_row_to_record(row) for row in rows]

    def delete(self, memory_id: str) -> bool:
        """Delete a record by id. Returns True only if a row was actually removed."""
        if self._unavailable:
            return False
        try:
            with self._connection() as conn:
                cur = conn.execute("DELETE FROM memory WHERE id = ?", (memory_id,))
                deleted = cur.rowcount > 0
        except sqlite3.Error as exc:
            warnings.warn(f"MemoryStore.delete failed: {exc}", RuntimeWarning)
            return False
        return deleted

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        agent_id: Optional[str] = None,
    ) -> list:
        """Deterministic keyword-overlap search over task+content.

        Not a vector/embedding search — scores each record by how many
        distinct lowercase word tokens it shares with the query, keeps only
        records with at least one match, and ranks by (overlap, recency).
        Returns [] if unavailable, the query is blank, or nothing matches.
        """
        if self._unavailable or not query or not query.strip():
            return []

        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        try:
            with self._connection() as conn:
                if agent_id is not None:
                    rows = conn.execute(
                        "SELECT * FROM memory WHERE agent_id = ?", (agent_id,)
                    ).fetchall()
                else:
                    rows = conn.execute("SELECT * FROM memory").fetchall()
        except sqlite3.Error as exc:
            warnings.warn(f"MemoryStore.search failed: {exc}", RuntimeWarning)
            return []

        scored = []
        for row in rows:
            record = _row_to_record(row)
            overlap = len(query_tokens & (_tokenize(record.task) | _tokenize(record.content)))
            if overlap > 0:
                scored.append((overlap, record.created_at, record))

        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [record for _, _, record in scored[:limit]]


default_memory = MemoryStore()
