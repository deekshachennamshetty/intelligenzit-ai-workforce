"""
Local RAG / Knowledge Base layer for the AI Workforce.

Ingests local company documents (.txt, .md, .pdf), extracts their text,
splits it into deterministic overlapping chunks, and stores document +
chunk metadata in a local SQLite database (stdlib sqlite3). Retrieval is
deterministic keyword-overlap scoring over chunk text — no embeddings, no
external/vector database.

This is deliberately a *different* store from backend/memory.py:
- backend/memory.py (MemoryStore) holds dynamic, execution-outcome memory —
  written automatically by AIExecutive after each workflow run, keyed by
  task/content.
- backend/knowledge_base.py (KnowledgeBase) holds static, human-curated
  company documents — populated only via explicit ingest() calls, never
  written to by a workflow run.
They live in separate files, separate SQLite databases, and separate
schemas, and are surfaced to the LLM as clearly labeled, separate sections
so the two kinds of context are never conflated.
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

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "knowledge_base.sqlite3"

DEFAULT_CHUNK_SIZE = 800
DEFAULT_CHUNK_OVERLAP = 100

SUPPORTED_EXTENSIONS = {".txt", ".md", ".pdf"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    source_path TEXT NOT NULL,
    doc_type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    text TEXT NOT NULL
);
"""

_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> set:
    return set(_WORD_RE.findall((text or "").lower()))


@dataclass(frozen=True)
class KBDocument:
    id: str
    filename: str
    source_path: str
    doc_type: str  # "txt" | "md" | "pdf"
    created_at: str  # ISO 8601, UTC
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class KBChunk:
    id: str
    document_id: str
    chunk_index: int
    text: str


@dataclass(frozen=True)
class RetrievedChunk:
    """A search hit, carrying everything needed to trace it back to its
    source document (constraint: "retrieved sources must include
    document/chunk identifiers and relevant text")."""
    chunk_id: str
    document_id: str
    filename: str
    chunk_index: int
    text: str
    score: int


def _row_to_document(row: sqlite3.Row) -> KBDocument:
    return KBDocument(
        id=row["id"],
        filename=row["filename"],
        source_path=row["source_path"],
        doc_type=row["doc_type"],
        created_at=row["created_at"],
        metadata=json.loads(row["metadata"]) if row["metadata"] else {},
    )


def _row_to_chunk(row: sqlite3.Row) -> KBChunk:
    return KBChunk(
        id=row["id"],
        document_id=row["document_id"],
        chunk_index=row["chunk_index"],
        text=row["text"],
    )


def _chunk_text(text: str, *, chunk_size: int, overlap: int) -> list:
    """Deterministic fixed-size sliding-window chunking. Pure function of its
    inputs — same text/size/overlap always produces the same chunks."""
    text = (text or "").strip()
    if not text:
        return []
    if overlap < 0 or overlap >= chunk_size:
        overlap = max(0, chunk_size // 4)

    chunks = []
    start = 0
    length = len(text)
    while start < length:
        end = min(start + chunk_size, length)
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= length:
            break
        start = end - overlap
    return chunks


def _extract_text(path: Path) -> Optional[str]:
    """Extract plain text from a supported document. Returns None on any
    failure (unreadable file, corrupt PDF, etc.) rather than raising."""
    suffix = path.suffix.lower()
    try:
        if suffix in (".txt", ".md"):
            return path.read_text(encoding="utf-8", errors="replace")
        if suffix == ".pdf":
            from pypdf import PdfReader  # stdlib has no PDF support; lazy import
            reader = PdfReader(str(path))
            return "\n\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception:
        return None
    return None


class KnowledgeBase:
    """SQLite-backed local knowledge base of ingested documents and their
    chunks. One connection per operation (short-lived, always closed).
    Fails safe: if the database file/directory can't be opened, every
    public method degrades to a harmless no-op (ingest() returns None,
    search() returns [], get_document() returns None, delete_document()
    returns False) instead of raising.
    """

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
                conn.executescript(_SCHEMA)
        except (sqlite3.Error, OSError) as exc:
            self._unavailable = True
            warnings.warn(
                f"KnowledgeBase: database unavailable at {self.db_path} ({exc}); "
                "the knowledge base will be a no-op for this session.",
                RuntimeWarning,
            )

    @contextmanager
    def _connection(self):
        conn = sqlite3.connect(str(self.db_path), timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def ingest(
        self,
        source_path,
        *,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
        metadata: Optional[dict] = None,
    ) -> Optional[KBDocument]:
        """Extract, chunk, and store a document. Returns None (and warns) if
        the store is unavailable, the path isn't a supported file, or no
        text could be extracted — never raises for an invalid document."""
        if self._unavailable:
            return None

        path = Path(source_path)
        if not path.is_file():
            warnings.warn(f"KnowledgeBase.ingest: not a file: {path}", RuntimeWarning)
            return None

        suffix = path.suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            warnings.warn(
                f"KnowledgeBase.ingest: unsupported file type '{suffix}' for {path} "
                f"(supported: {sorted(SUPPORTED_EXTENSIONS)})",
                RuntimeWarning,
            )
            return None

        text = _extract_text(path)
        if not text or not text.strip():
            warnings.warn(f"KnowledgeBase.ingest: no extractable text in {path}", RuntimeWarning)
            return None

        chunks = _chunk_text(text, chunk_size=chunk_size, overlap=chunk_overlap)
        if not chunks:
            warnings.warn(f"KnowledgeBase.ingest: produced zero chunks for {path}", RuntimeWarning)
            return None

        document = KBDocument(
            id=str(uuid.uuid4()),
            filename=path.name,
            source_path=str(path),
            doc_type=suffix.lstrip("."),
            created_at=datetime.now(timezone.utc).isoformat(),
            metadata=dict(metadata or {}),
        )
        try:
            with self._connection() as conn:
                conn.execute(
                    "INSERT INTO documents (id, filename, source_path, doc_type, created_at, metadata) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        document.id, document.filename, document.source_path, document.doc_type,
                        document.created_at, json.dumps(document.metadata),
                    ),
                )
                conn.executemany(
                    "INSERT INTO chunks (id, document_id, chunk_index, text) VALUES (?, ?, ?, ?)",
                    [(str(uuid.uuid4()), document.id, idx, chunk) for idx, chunk in enumerate(chunks)],
                )
        except sqlite3.Error as exc:
            warnings.warn(f"KnowledgeBase.ingest failed: {exc}", RuntimeWarning)
            return None
        return document

    def get_document(self, document_id: str) -> Optional[KBDocument]:
        if self._unavailable:
            return None
        try:
            with self._connection() as conn:
                row = conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
        except sqlite3.Error as exc:
            warnings.warn(f"KnowledgeBase.get_document failed: {exc}", RuntimeWarning)
            return None
        return _row_to_document(row) if row else None

    def get_chunks(self, document_id: str) -> list:
        """All chunks for a document, in order. Mainly for inspection/tests."""
        if self._unavailable:
            return []
        try:
            with self._connection() as conn:
                rows = conn.execute(
                    "SELECT * FROM chunks WHERE document_id = ? ORDER BY chunk_index", (document_id,)
                ).fetchall()
        except sqlite3.Error as exc:
            warnings.warn(f"KnowledgeBase.get_chunks failed: {exc}", RuntimeWarning)
            return []
        return [_row_to_chunk(row) for row in rows]

    def delete_document(self, document_id: str) -> bool:
        """Delete a document and (via ON DELETE CASCADE) all its chunks."""
        if self._unavailable:
            return False
        try:
            with self._connection() as conn:
                cur = conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
                deleted = cur.rowcount > 0
        except sqlite3.Error as exc:
            warnings.warn(f"KnowledgeBase.delete_document failed: {exc}", RuntimeWarning)
            return False
        return deleted

    def search(self, query: str, *, limit: int = 5) -> list:
        """Deterministic keyword-overlap search over chunk text.

        Scores each chunk by how many distinct lowercase word tokens it
        shares with the query, keeps only chunks with at least one match,
        and ranks by (overlap, source document recency). Returns [] if
        unavailable, the query is blank, or nothing matches.
        """
        if self._unavailable or not query or not query.strip():
            return []

        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        try:
            with self._connection() as conn:
                rows = conn.execute(
                    "SELECT chunks.id AS chunk_id, chunks.document_id AS document_id, "
                    "chunks.chunk_index AS chunk_index, chunks.text AS text, "
                    "documents.filename AS filename, documents.created_at AS created_at "
                    "FROM chunks JOIN documents ON chunks.document_id = documents.id"
                ).fetchall()
        except sqlite3.Error as exc:
            warnings.warn(f"KnowledgeBase.search failed: {exc}", RuntimeWarning)
            return []

        scored = []
        for row in rows:
            overlap = len(query_tokens & _tokenize(row["text"]))
            if overlap > 0:
                chunk = RetrievedChunk(
                    chunk_id=row["chunk_id"],
                    document_id=row["document_id"],
                    filename=row["filename"],
                    chunk_index=row["chunk_index"],
                    text=row["text"],
                    score=overlap,
                )
                scored.append((overlap, row["created_at"], chunk))

        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [item[2] for item in scored[:limit]]


default_knowledge_base = KnowledgeBase()
