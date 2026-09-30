"""
Tests for backend/audit.py: AuditEvent, AuditStore persistence, querying
(recent / by_request / by_agent / by_tool), redaction, append-only
behavior (no update/delete API exists at all), and DB-failure isolation.
"""
import pytest

from backend.audit import AuditEvent, AuditStore, default_audit_store, redact_metadata


def make_store(tmp_path, name="audit.sqlite3") -> AuditStore:
    return AuditStore(db_path=tmp_path / name)


# --- append / persistence ---------------------------------------------

def test_append_returns_an_audit_event_with_generated_id_and_timestamp(tmp_path):
    store = make_store(tmp_path)
    event = store.append(actor="tool_registry", action="tool_execute", status="success")

    assert isinstance(event, AuditEvent)
    assert event.id
    assert event.timestamp
    assert event.actor == "tool_registry"
    assert event.action == "tool_execute"
    assert event.status == "success"
    assert event.metadata == {}


def test_append_stores_all_fields(tmp_path):
    store = make_store(tmp_path)
    event = store.append(
        actor="ai_executive", action="dispatch", status="complete",
        request_id="req-1", agent_id="solution", tool_id="calculator",
        decision="approve", metadata={"note": "ok"},
    )

    fetched = store.by_request("req-1")[0]
    assert fetched == event
    assert fetched.request_id == "req-1"
    assert fetched.agent_id == "solution"
    assert fetched.tool_id == "calculator"
    assert fetched.decision == "approve"
    assert fetched.metadata == {"note": "ok"}


def test_append_generates_unique_ids(tmp_path):
    store = make_store(tmp_path)
    e1 = store.append(actor="a", action="x", status="ok")
    e2 = store.append(actor="a", action="x", status="ok")
    assert e1.id != e2.id


# --- querying ------------------------------------------------------------

def test_recent_returns_most_recent_first(tmp_path):
    store = make_store(tmp_path)
    first = store.append(actor="a", action="x", status="ok")
    second = store.append(actor="a", action="x", status="ok")
    third = store.append(actor="a", action="x", status="ok")

    results = store.recent()

    assert [e.id for e in results] == [third.id, second.id, first.id]


def test_recent_respects_limit(tmp_path):
    store = make_store(tmp_path)
    for _ in range(10):
        store.append(actor="a", action="x", status="ok")
    assert len(store.recent(limit=3)) == 3


def test_by_request_filters_correctly(tmp_path):
    store = make_store(tmp_path)
    store.append(actor="a", action="x", status="ok", request_id="req-1")
    store.append(actor="a", action="x", status="ok", request_id="req-2")

    results = store.by_request("req-1")

    assert len(results) == 1
    assert results[0].request_id == "req-1"


def test_by_agent_filters_correctly(tmp_path):
    store = make_store(tmp_path)
    store.append(actor="a", action="x", status="ok", agent_id="solution")
    store.append(actor="a", action="x", status="ok", agent_id="review")

    results = store.by_agent("review")

    assert len(results) == 1
    assert results[0].agent_id == "review"


def test_by_tool_filters_correctly(tmp_path):
    store = make_store(tmp_path)
    store.append(actor="a", action="x", status="ok", tool_id="calculator")
    store.append(actor="a", action="x", status="ok", tool_id="send_invoice")

    results = store.by_tool("calculator")

    assert len(results) == 1
    assert results[0].tool_id == "calculator"


def test_query_combines_multiple_filters(tmp_path):
    store = make_store(tmp_path)
    store.append(actor="tool_registry", action="tool_execute", status="ok",
                 request_id="req-1", agent_id="solution", tool_id="calculator")
    store.append(actor="tool_registry", action="tool_execute", status="ok",
                 request_id="req-1", agent_id="review", tool_id="calculator")

    results = store.query(request_id="req-1", agent_id="solution")

    assert len(results) == 1
    assert results[0].agent_id == "solution"


def test_query_with_no_matches_returns_empty_list(tmp_path):
    store = make_store(tmp_path)
    store.append(actor="a", action="x", status="ok")
    assert store.query(request_id="does-not-exist") == []


# --- append-only behavior --------------------------------------------------

def test_audit_store_exposes_no_update_or_delete_method(tmp_path):
    store = make_store(tmp_path)
    assert not hasattr(store, "delete")
    assert not hasattr(store, "update")
    assert not hasattr(store, "remove")


def test_events_persist_unchanged_across_many_appends(tmp_path):
    store = make_store(tmp_path)
    first = store.append(actor="a", action="x", status="ok", metadata={"n": 1})
    for _ in range(5):
        store.append(actor="a", action="y", status="ok")

    match = [e for e in store.recent(limit=10) if e.id == first.id][0]
    assert match == first


def test_len_counts_all_appended_events(tmp_path):
    store = make_store(tmp_path)
    assert len(store) == 0
    store.append(actor="a", action="x", status="ok")
    store.append(actor="a", action="x", status="ok")
    assert len(store) == 2


# --- redaction ----------------------------------------------------------

@pytest.mark.parametrize("key", [
    "api_key", "password", "secret", "token", "credential", "authorization",
    "webhook_url", "prompt", "raw_prompt", "llm_response", "auth_header",
])
def test_redact_metadata_blocks_sensitive_keys(key):
    redacted = redact_metadata({key: "super-secret-value-12345"})
    assert redacted[key] == "***REDACTED***"


def test_redact_metadata_keeps_innocuous_keys():
    redacted = redact_metadata({"status": "success", "count": 3})
    assert redacted == {"status": "success", "count": 3}


def test_redact_metadata_truncates_long_strings():
    long_value = "x" * 5000
    redacted = redact_metadata({"note": long_value})
    assert len(redacted["note"]) < 5000
    assert redacted["note"].endswith("...[truncated]")


def test_redact_metadata_recurses_into_nested_dicts():
    redacted = redact_metadata({"outer": {"api_key": "leak-me", "fine": "ok"}})
    assert redacted["outer"]["api_key"] == "***REDACTED***"
    assert redacted["outer"]["fine"] == "ok"


def test_redact_metadata_recurses_into_lists():
    redacted = redact_metadata({"items": [{"token": "leak-me"}, {"ok": "fine"}]})
    assert redacted["items"][0]["token"] == "***REDACTED***"
    assert redacted["items"][1]["ok"] == "fine"


def test_redact_metadata_handles_non_dict_input_safely():
    assert redact_metadata(None) == {}
    assert redact_metadata("not a dict") == {}


def test_append_redacts_metadata_before_persisting(tmp_path):
    store = make_store(tmp_path)
    event = store.append(
        actor="tool_registry", action="tool_execute", status="ok",
        metadata={"api_key": "sk-super-secret", "webhook_url": "https://n8n.example.com/webhook/xyz"},
    )

    assert event.metadata["api_key"] == "***REDACTED***"
    assert event.metadata["webhook_url"] == "***REDACTED***"

    refetched = store.recent(limit=1)[0]
    assert refetched.metadata["api_key"] == "***REDACTED***"
    assert "sk-super-secret" not in str(refetched)


# --- DB-failure isolation ------------------------------------------------

def test_fails_safe_when_db_directory_cannot_be_created(tmp_path):
    blocking_file = tmp_path / "not_a_directory"
    blocking_file.write_text("i am a file, not a directory")
    bad_db_path = blocking_file / "sub" / "audit.sqlite3"

    with pytest.warns(RuntimeWarning):
        store = AuditStore(db_path=bad_db_path)

    assert store.is_available is False
    assert store.append(actor="a", action="x", status="ok") is None
    assert store.recent() == []
    assert store.by_request("req-1") == []
    assert store.by_agent("agent-1") == []
    assert store.by_tool("tool-1") == []
    assert len(store) == 0


def test_available_store_reports_is_available_true(tmp_path):
    assert make_store(tmp_path).is_available is True


def test_default_audit_store_is_an_audit_store():
    assert isinstance(default_audit_store, AuditStore)
