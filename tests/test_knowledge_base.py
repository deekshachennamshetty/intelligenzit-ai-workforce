"""
Tests for backend/knowledge_base.py: ingestion (.txt/.md/.pdf), chunking,
persistence, search ranking, deletion (with chunk cascade), and fail-safe
degradation when the database or a document is invalid.
"""
import pytest

from backend.knowledge_base import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    KBDocument,
    KnowledgeBase,
    RetrievedChunk,
    _chunk_text,
    default_knowledge_base,
)


def make_kb(tmp_path, name="kb.sqlite3") -> KnowledgeBase:
    return KnowledgeBase(db_path=tmp_path / name)


def _make_minimal_pdf(path, text: str) -> None:
    """Hand-build the smallest valid single-page PDF with real extractable
    text, so ingestion can be tested without a PDF-authoring dependency.
    `text` must not contain '(' ')' or backslashes (kept simple for tests)."""
    content = f"BT /F1 24 Tf 10 100 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> "
        b"/MediaBox [0 0 300 144] /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
    ]
    pdf = b"%PDF-1.4\n"
    offsets = [0]
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf += str(i).encode() + b" 0 obj\n" + obj + b"\nendobj\n"
    xref_offset = len(pdf)
    pdf += b"xref\n0 " + str(len(objects) + 1).encode() + b"\n0000000000 65535 f \n"
    for off in offsets[1:]:
        pdf += ("%010d 00000 n \n" % off).encode()
    pdf += (
        b"trailer\n<< /Size " + str(len(objects) + 1).encode() + b" /Root 1 0 R >>\n"
        b"startxref\n" + str(xref_offset).encode() + b"\n%%EOF"
    )
    path.write_bytes(pdf)


# --- chunking (pure function) -----------------------------------------------

def test_chunk_text_splits_long_text_with_overlap():
    text = "word " * 500  # 2500 chars
    chunks = _chunk_text(text, chunk_size=800, overlap=100)
    assert len(chunks) > 1
    assert all(len(c) <= 800 for c in chunks)


def test_chunk_text_returns_single_chunk_for_short_text():
    chunks = _chunk_text("A short document.", chunk_size=800, overlap=100)
    assert chunks == ["A short document."]


def test_chunk_text_returns_empty_list_for_blank_text():
    assert _chunk_text("", chunk_size=800, overlap=100) == []
    assert _chunk_text("   \n\n  ", chunk_size=800, overlap=100) == []


def test_chunk_text_is_deterministic():
    text = "The quick brown fox jumps over the lazy dog. " * 30
    assert _chunk_text(text, chunk_size=200, overlap=50) == _chunk_text(text, chunk_size=200, overlap=50)


# --- ingest: .txt / .md -----------------------------------------------------

def test_ingest_txt_file_stores_document_and_chunks(tmp_path):
    kb = make_kb(tmp_path)
    doc_path = tmp_path / "policy.txt"
    doc_path.write_text("This is the company's remote work policy. Employees may work from home.")

    document = kb.ingest(doc_path)

    assert isinstance(document, KBDocument)
    assert document.filename == "policy.txt"
    assert document.doc_type == "txt"
    chunks = kb.get_chunks(document.id)
    assert len(chunks) >= 1
    assert "remote work policy" in chunks[0].text


def test_ingest_md_file(tmp_path):
    kb = make_kb(tmp_path)
    doc_path = tmp_path / "onboarding.md"
    doc_path.write_text("# Onboarding\n\nWelcome to the company. Here is how to set up your laptop.")

    document = kb.ingest(doc_path)

    assert document.doc_type == "md"
    chunks = kb.get_chunks(document.id)
    assert "Onboarding" in chunks[0].text


def test_ingest_respects_custom_chunk_size(tmp_path):
    kb = make_kb(tmp_path)
    doc_path = tmp_path / "long.txt"
    doc_path.write_text("word " * 500)

    document = kb.ingest(doc_path, chunk_size=100, chunk_overlap=20)

    chunks = kb.get_chunks(document.id)
    assert len(chunks) > 1
    assert all(len(c.text) <= 100 for c in chunks)


def test_ingest_stores_metadata(tmp_path):
    kb = make_kb(tmp_path)
    doc_path = tmp_path / "policy.txt"
    doc_path.write_text("Some policy content.")

    document = kb.ingest(doc_path, metadata={"department": "HR"})

    assert document.metadata == {"department": "HR"}
    assert kb.get_document(document.id).metadata == {"department": "HR"}


# --- ingest: .pdf ------------------------------------------------------------

def test_ingest_pdf_file_extracts_text(tmp_path):
    kb = make_kb(tmp_path)
    pdf_path = tmp_path / "handbook.pdf"
    _make_minimal_pdf(pdf_path, "Hello Knowledge Base")

    document = kb.ingest(pdf_path)

    assert document is not None
    assert document.doc_type == "pdf"
    chunks = kb.get_chunks(document.id)
    assert len(chunks) == 1
    assert "Hello Knowledge Base" in chunks[0].text


# --- ingest: invalid documents fail safe ------------------------------------

def test_ingest_missing_file_returns_none(tmp_path):
    kb = make_kb(tmp_path)
    with pytest.warns(RuntimeWarning):
        result = kb.ingest(tmp_path / "does_not_exist.txt")
    assert result is None


def test_ingest_unsupported_extension_returns_none(tmp_path):
    kb = make_kb(tmp_path)
    doc_path = tmp_path / "image.png"
    doc_path.write_bytes(b"not really a png but irrelevant")

    with pytest.warns(RuntimeWarning):
        result = kb.ingest(doc_path)
    assert result is None


def test_ingest_empty_text_file_returns_none(tmp_path):
    kb = make_kb(tmp_path)
    doc_path = tmp_path / "empty.txt"
    doc_path.write_text("   \n\n  ")

    with pytest.warns(RuntimeWarning):
        result = kb.ingest(doc_path)
    assert result is None


def test_ingest_corrupt_pdf_returns_none(tmp_path):
    kb = make_kb(tmp_path)
    doc_path = tmp_path / "corrupt.pdf"
    doc_path.write_bytes(b"not a real pdf at all")

    result = kb.ingest(doc_path)  # must not raise
    assert result is None


# --- get_document / get_chunks ----------------------------------------------

def test_get_document_missing_returns_none(tmp_path):
    kb = make_kb(tmp_path)
    assert kb.get_document("does-not-exist") is None


def test_get_chunks_for_missing_document_returns_empty_list(tmp_path):
    kb = make_kb(tmp_path)
    assert kb.get_chunks("does-not-exist") == []


# --- delete_document (with cascade) ------------------------------------------

def test_delete_document_removes_document_and_its_chunks(tmp_path):
    kb = make_kb(tmp_path)
    doc_path = tmp_path / "policy.txt"
    doc_path.write_text("Some policy content to delete later.")
    document = kb.ingest(doc_path)

    assert kb.delete_document(document.id) is True
    assert kb.get_document(document.id) is None
    assert kb.get_chunks(document.id) == []


def test_delete_missing_document_returns_false(tmp_path):
    kb = make_kb(tmp_path)
    assert kb.delete_document("does-not-exist") is False


def test_deleted_document_chunks_no_longer_appear_in_search(tmp_path):
    kb = make_kb(tmp_path)
    doc_path = tmp_path / "policy.txt"
    doc_path.write_text("Unique searchable phrase about vacation policy.")
    document = kb.ingest(doc_path)
    assert kb.search("vacation policy")

    kb.delete_document(document.id)

    assert kb.search("vacation policy") == []


# --- search ------------------------------------------------------------------

def test_search_finds_relevant_chunk_with_traceable_identifiers(tmp_path):
    kb = make_kb(tmp_path)
    doc_path = tmp_path / "sap_integration.txt"
    doc_path.write_text("Guidelines for SAP integration and inventory modernisation projects.")
    document = kb.ingest(doc_path)

    results = kb.search("SAP inventory modernisation")

    assert len(results) == 1
    hit = results[0]
    assert isinstance(hit, RetrievedChunk)
    assert hit.document_id == document.id
    assert hit.filename == "sap_integration.txt"
    assert hit.chunk_index == 0
    assert "SAP integration" in hit.text
    assert hit.score > 0


def test_search_returns_empty_for_blank_query(tmp_path):
    kb = make_kb(tmp_path)
    doc_path = tmp_path / "a.txt"
    doc_path.write_text("content")
    kb.ingest(doc_path)
    assert kb.search("") == []
    assert kb.search("   ") == []


def test_search_returns_empty_when_nothing_matches(tmp_path):
    kb = make_kb(tmp_path)
    doc_path = tmp_path / "a.txt"
    doc_path.write_text("Completely unrelated content about gardening.")
    kb.ingest(doc_path)
    assert kb.search("quantum spaceship unicorn") == []


def test_search_respects_limit(tmp_path):
    kb = make_kb(tmp_path)
    for i in range(10):
        doc_path = tmp_path / f"doc_{i}.txt"
        doc_path.write_text("inventory system content")
        kb.ingest(doc_path)

    assert len(kb.search("inventory", limit=3)) == 3


def test_search_ranks_higher_overlap_first(tmp_path):
    kb = make_kb(tmp_path)
    low_path = tmp_path / "low.txt"
    low_path.write_text("inventory only")
    high_path = tmp_path / "high.txt"
    high_path.write_text("inventory logistics SAP migration")

    kb.ingest(low_path)
    kb.ingest(high_path)

    results = kb.search("inventory logistics SAP migration")
    assert results[0].filename == "high.txt"


# --- persistence across "restarts" ------------------------------------------

def test_data_persists_across_new_instances_pointed_at_same_file(tmp_path):
    db_path = tmp_path / "restart.sqlite3"
    doc_path = tmp_path / "policy.txt"
    doc_path.write_text("Persisted knowledge base content.")

    kb1 = KnowledgeBase(db_path=db_path)
    document = kb1.ingest(doc_path)
    del kb1  # simulate the process ending

    kb2 = KnowledgeBase(db_path=db_path)  # simulate the app restarting
    assert kb2.get_document(document.id) == document
    assert kb2.search("Persisted knowledge base") != []


# --- fail-safe behavior ---------------------------------------------------

def test_fails_safe_when_db_directory_cannot_be_created(tmp_path):
    blocking_file = tmp_path / "not_a_directory"
    blocking_file.write_text("i am a file, not a directory")
    bad_db_path = blocking_file / "sub" / "kb.sqlite3"

    with pytest.warns(RuntimeWarning):
        kb = KnowledgeBase(db_path=bad_db_path)

    assert kb.is_available is False
    assert kb.ingest(tmp_path / "whatever.txt") is None
    assert kb.search("anything") == []
    assert kb.get_document("anything") is None
    assert kb.get_chunks("anything") == []
    assert kb.delete_document("anything") is False


def test_available_kb_reports_is_available_true(tmp_path):
    assert make_kb(tmp_path).is_available is True


def test_default_knowledge_base_singleton_is_a_knowledge_base():
    assert isinstance(default_knowledge_base, KnowledgeBase)


def test_default_chunk_constants_are_sane():
    assert DEFAULT_CHUNK_SIZE > DEFAULT_CHUNK_OVERLAP > 0
