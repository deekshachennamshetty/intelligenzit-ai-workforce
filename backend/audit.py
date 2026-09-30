"""
Governance & Audit layer for the AI Workforce: an append-only, local record
of who/what triggered which action, and what happened — kept entirely
separate from backend/memory.py (execution memory, used for LLM context)
and backend/knowledge_base.py (RAG documents). Audit exists purely for
traceability/oversight, never fed back into a prompt.

Safety properties:
- Append-only: AuditStore exposes no update or delete method at all — the
  only way to change what's recorded is to add a new event.
- Secrets are never stored by default: every metadata dict passed to
  append() is redacted before it touches disk (redact_metadata()) — any
  key that looks like it might hold a password, token, API key, webhook
  URL, credential, or raw prompt/LLM response is replaced outright, and
  every remaining string value is length-capped, so even an unexpectedly
  large blob (e.g. a full LLM response slipped in under an innocuous key)
  can't end up fully persisted. Callers should still avoid passing secrets
  in the first place — this is a backstop, not a license to be careless.
- Fails safe: like MemoryStore and KnowledgeBase, if the database can't be
  opened, every public method degrades to a harmless no-op (append()
  returns None, every query returns []) instead of raising, and callers in
  AIExecutive/ToolRegistry/N8NRegistry wrap their audit calls in a second
  try/except — an audit failure can never block or crash normal execution.
"""
import json
import sqlite3
import uuid
import warnings
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "audit.sqlite3"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    request_id TEXT,
    actor TEXT NOT NULL,
    agent_id TEXT,
    tool_id TEXT,
    action TEXT NOT NULL,
    status TEXT NOT NULL,
    decision TEXT,
    metadata TEXT NOT NULL DEFAULT '{}'
)
"""

# Substring match, case-insensitive, against metadata keys. Intentionally
# broad (e.g. "key" also catches harmless fields like "primary_key") —
# over-redaction is the safe default for an audit trail.
_SENSITIVE_KEY_SUBSTRINGS = (
    "password", "secret", "token", "key", "credential", "auth",
    "webhook", "prompt", "response",
)
_REDACTED_VALUE = "***REDACTED***"
_MAX_METADATA_STRING_LENGTH = 200


def _is_sensitive_key(key: str) -> bool:
    lowered = str(key).lower()
    return any(substring in lowered for substring in _SENSITIVE_KEY_SUBSTRINGS)


def _redact_value(value):
    if isinstance(value, str):
        if len(value) > _MAX_METADATA_STRING_LENGTH:
            return value[:_MAX_METADATA_STRING_LENGTH] + "...[truncated]"
        return value
    if isinstance(value, dict):
        return redact_metadata(value)
    if isinstance(value, (list, tuple)):
        return [_redact_value(item) for item in value]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _redact_value(repr(value))  # anything else -> a bounded string, not the raw object


def redact_metadata(metadata: dict) -> dict:
    """Return a copy of `metadata` safe to persist: any key that looks
    sensitive (see _SENSITIVE_KEY_SUBSTRINGS) is fully replaced, and every
    remaining string value (recursively, through nested dicts/lists) is
    length-capped. Never raises — a metadata dict that isn't actually a
    dict is treated as empty."""
    if not isinstance(metadata, dict):
        return {}
    redacted = {}
    for key, value in metadata.items():
        if _is_sensitive_key(key):
            redacted[key] = _REDACTED_VALUE
        else:
            redacted[key] = _redact_value(value)
    return redacted


@dataclass(frozen=True)
class AuditEvent:
    id: str
    timestamp: str  # ISO 8601, UTC
    request_id: Optional[str]
    actor: str  # which component recorded this: "ai_executive" | "tool_registry" | "n8n" | ...
    agent_id: Optional[str]
    tool_id: Optional[str]  # for n8n events, this holds the n8n workflow_id
    action: str  # e.g. "plan", "dispatch", "tool_execute", "n8n_execute"
    status: str  # e.g. "success", "failure", "fallback", or a specific error_code
    decision: Optional[str]  # e.g. a review decision ("approve"/"rework"), if applicable
    metadata: dict = field(default_factory=dict)


def _row_to_event(row: sqlite3.Row) -> AuditEvent:
    return AuditEvent(
        id=row["id"],
        timestamp=row["timestamp"],
        request_id=row["request_id"],
        actor=row["actor"],
        agent_id=row["agent_id"],
        tool_id=row["tool_id"],
        action=row["action"],
        status=row["status"],
        decision=row["decision"],
        metadata=json.loads(row["metadata"]) if row["metadata"] else {},
    )


class AuditStore:
    """Append-only, SQLite-backed audit log. One connection per operation
    (short-lived, always closed). Fails safe: an unavailable database makes
    append() a no-op (returns None) and every query return []."""

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
                f"AuditStore: database unavailable at {self.db_path} ({exc}); "
                "audit logging will be a no-op for this session.",
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

    def append(
        self,
        *,
        actor: str,
        action: str,
        status: str,
        request_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        tool_id: Optional[str] = None,
        decision: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> Optional[AuditEvent]:
        """Append one audit record — the only way to write to this store;
        there is no update or delete. metadata is always redacted before
        being persisted. Returns None (no-op) if the store is unavailable
        or the insert fails; never raises."""
        if self._unavailable:
            return None

        event = AuditEvent(
            id=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc).isoformat(),
            request_id=request_id,
            actor=actor,
            agent_id=agent_id,
            tool_id=tool_id,
            action=action,
            status=status,
            decision=decision,
            metadata=redact_metadata(metadata or {}),
        )
        try:
            with self._connection() as conn:
                conn.execute(
                    "INSERT INTO audit_events "
                    "(id, timestamp, request_id, actor, agent_id, tool_id, action, status, decision, metadata) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        event.id, event.timestamp, event.request_id, event.actor, event.agent_id,
                        event.tool_id, event.action, event.status, event.decision, json.dumps(event.metadata),
                    ),
                )
        except sqlite3.Error as exc:
            warnings.warn(f"AuditStore.append failed: {exc}", RuntimeWarning)
            return None
        return event

    def query(
        self,
        *,
        request_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        tool_id: Optional[str] = None,
        actor: Optional[str] = None,
        limit: int = 50,
    ) -> list:
        """Most-recent-first, optionally filtered. Returns [] if unavailable
        or on any query error."""
        if self._unavailable:
            return []

        clauses, params = [], []
        for column, value in (("request_id", request_id), ("agent_id", agent_id),
                               ("tool_id", tool_id), ("actor", actor)):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)

        try:
            with self._connection() as conn:
                rows = conn.execute(
                    f"SELECT * FROM audit_events {where} ORDER BY rowid DESC LIMIT ?", params,
                ).fetchall()
        except sqlite3.Error as exc:
            warnings.warn(f"AuditStore.query failed: {exc}", RuntimeWarning)
            return []
        return [_row_to_event(row) for row in rows]

    def recent(self, limit: int = 50) -> list:
        return self.query(limit=limit)

    def by_request(self, request_id: str, limit: int = 50) -> list:
        return self.query(request_id=request_id, limit=limit)

    def by_agent(self, agent_id: str, limit: int = 50) -> list:
        return self.query(agent_id=agent_id, limit=limit)

    def by_tool(self, tool_id: str, limit: int = 50) -> list:
        return self.query(tool_id=tool_id, limit=limit)

    def __len__(self) -> int:
        if self._unavailable:
            return 0
        try:
            with self._connection() as conn:
                return conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
        except sqlite3.Error:
            return 0


default_audit_store = AuditStore()
