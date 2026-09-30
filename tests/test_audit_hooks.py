"""
Tests for the audit hooks wired into AIExecutive, ToolRegistry, and
N8NRegistry: each records a structured event for its own execution path,
none of them ever store secrets/raw inputs, and a completely unavailable
audit backend never blocks or crashes normal execution.
"""
import urllib.error

import pytest

from backend.agent_registry import default_registry
from backend.audit import AuditStore
from backend.ai_executive import AIExecutive, DEFAULT_PIPELINE_ORDER
from backend.knowledge_base import KnowledgeBase
from backend.memory import MemoryStore
from backend.n8n import N8NRegistry, N8NWorkflowDefinition, make_n8n_tool
from backend.tool_registry import ToolParameter, ToolRegistry


def broken_audit_store(tmp_path) -> AuditStore:
    blocking_file = tmp_path / "not_a_directory"
    blocking_file.write_text("blocker")
    with pytest.warns(RuntimeWarning):
        return AuditStore(db_path=blocking_file / "sub" / "audit.sqlite3")


# --- ToolRegistry hook -------------------------------------------------

def test_tool_execute_records_success_event(tmp_path):
    audit = AuditStore(db_path=tmp_path / "audit.sqlite3")
    registry = ToolRegistry(audit_store=audit)
    registry.register(from_calculator())

    registry.execute("calculator", {"expression": "2+2"}, agent_id="solution", request_id="req-1")

    events = audit.by_tool("calculator")
    assert len(events) == 1
    event = events[0]
    assert event.actor == "tool_registry"
    assert event.action == "tool_execute"
    assert event.status == "success"
    assert event.agent_id == "solution"
    assert event.request_id == "req-1"


def test_tool_execute_records_failure_event_without_leaking_input_values(tmp_path):
    audit = AuditStore(db_path=tmp_path / "audit.sqlite3")
    registry = ToolRegistry(audit_store=audit)
    registry.register(from_calculator())

    registry.execute("calculator", {"expression": "not-a-real-expression-with-secret-token"})

    event = audit.by_tool("calculator")[0]
    assert event.status == "execution_error"
    assert event.metadata == {"input_names": ["expression"]}
    assert "secret-token" not in str(event)


def test_tool_execute_records_unknown_tool_event(tmp_path):
    audit = AuditStore(db_path=tmp_path / "audit.sqlite3")
    registry = ToolRegistry(audit_store=audit)

    registry.execute("does-not-exist", {})

    event = audit.by_tool("does-not-exist")[0]
    assert event.status == "unknown_tool"


def test_tool_execute_still_works_when_audit_store_unavailable(tmp_path):
    audit = broken_audit_store(tmp_path)
    registry = ToolRegistry(audit_store=audit)
    registry.register(from_calculator())

    result = registry.execute("calculator", {"expression": "6*7"})

    assert result.success is True
    assert result.output == 42


def from_calculator():
    from backend.tool_registry import CALCULATOR_TOOL
    return CALCULATOR_TOOL


# --- N8NRegistry hook ----------------------------------------------------

class _FakeResponse:
    def __init__(self, status, body: bytes):
        self._status, self._body = status, body
    def getcode(self):
        return self._status
    def read(self):
        return self._body
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def make_workflow(**overrides) -> N8NWorkflowDefinition:
    fields = dict(
        workflow_id="send_invoice",
        name="Send Invoice",
        webhook_url="https://n8n.example.com/webhook/super-secret-path",
        allowed_methods=("POST",),
    )
    fields.update(overrides)
    return N8NWorkflowDefinition(**fields)


def test_n8n_execute_records_success_event_without_leaking_the_url(tmp_path, monkeypatch):
    audit = AuditStore(db_path=tmp_path / "audit.sqlite3")
    registry = N8NRegistry(audit_store=audit)
    registry.register(make_workflow())
    monkeypatch.setattr("backend.n8n.urllib.request.urlopen", lambda req, timeout=None: _FakeResponse(200, b"{}"))

    registry.execute("send_invoice", {"amount": 10}, agent_id="solution", request_id="req-2")

    event = audit.by_tool("send_invoice")[0]
    assert event.actor == "n8n"
    assert event.action == "n8n_execute"
    assert event.status == "success"
    assert event.agent_id == "solution"
    assert event.request_id == "req-2"
    assert "super-secret-path" not in str(event)


def test_n8n_execute_records_failure_event_without_leaking_the_url(tmp_path, monkeypatch):
    audit = AuditStore(db_path=tmp_path / "audit.sqlite3")
    registry = N8NRegistry(audit_store=audit)
    registry.register(make_workflow())
    monkeypatch.setattr(
        "backend.n8n.urllib.request.urlopen",
        lambda req, timeout=None: (_ for _ in ()).throw(urllib.error.URLError("connection refused")),
    )

    registry.execute("send_invoice", {})

    event = audit.by_tool("send_invoice")[0]
    assert event.status == "network_error"
    assert "super-secret-path" not in str(event)


def test_n8n_execute_still_works_when_audit_store_unavailable(tmp_path, monkeypatch):
    audit = broken_audit_store(tmp_path)
    registry = N8NRegistry(audit_store=audit)
    registry.register(make_workflow())
    monkeypatch.setattr("backend.n8n.urllib.request.urlopen", lambda req, timeout=None: _FakeResponse(200, b"{}"))

    result = registry.execute("send_invoice", {})

    assert result.success is True


def test_n8n_backed_tool_still_produces_a_tool_registry_audit_event(tmp_path, monkeypatch):
    """The ToolRegistry layer audits the tool call regardless of what the
    handler does underneath (including triggering n8n)."""
    tool_audit = AuditStore(db_path=tmp_path / "tool_audit.sqlite3")
    n8n_registry = N8NRegistry(audit_store=AuditStore(db_path=tmp_path / "n8n_audit.sqlite3"))
    n8n_registry.register(make_workflow())
    tool_registry = ToolRegistry(audit_store=tool_audit)
    tool_registry.register(make_n8n_tool(
        "trigger_invoice", "Trigger Invoice", "desc", "send_invoice", n8n_registry=n8n_registry,
        parameters=(ToolParameter(name="amount", type="float"),),
    ))
    monkeypatch.setattr("backend.n8n.urllib.request.urlopen", lambda req, timeout=None: _FakeResponse(200, b"{}"))

    tool_registry.execute("trigger_invoice", {"amount": 5.0})

    event = tool_audit.by_tool("trigger_invoice")[0]
    assert event.status == "success"


# --- AIExecutive hooks -------------------------------------------------

@pytest.fixture(autouse=True)
def mock_mode(monkeypatch):
    monkeypatch.setenv("MOCK_MODE", "1")


def make_executive(tmp_path, audit) -> AIExecutive:
    return AIExecutive(
        memory=MemoryStore(db_path=tmp_path / "mem.sqlite3"),
        knowledge_base=KnowledgeBase(db_path=tmp_path / "kb.sqlite3"),
        audit_store=audit,
    )


def _mock_plan_response(system, user, **kwargs):
    return {
        "task_type": "technology_recommendation",
        "agents": [{"agent_id": a, "reason": "part of pipeline"} for a in DEFAULT_PIPELINE_ORDER],
    }


def test_plan_records_a_plan_audit_event(monkeypatch, tmp_path):
    audit = AuditStore(db_path=tmp_path / "audit.sqlite3")
    executive = make_executive(tmp_path, audit)
    monkeypatch.setattr("backend.ai_executive.call_llm_json", _mock_plan_response)

    executive.plan("A client task about inventory.")

    events = audit.recent()
    assert len(events) == 1
    event = events[0]
    assert event.actor == "ai_executive"
    assert event.action == "plan"
    assert event.status == "success"
    assert event.metadata["selected_agents"] == list(DEFAULT_PIPELINE_ORDER)


def test_plan_audit_event_never_contains_the_raw_task_text(monkeypatch, tmp_path):
    audit = AuditStore(db_path=tmp_path / "audit.sqlite3")
    executive = make_executive(tmp_path, audit)
    monkeypatch.setattr("backend.ai_executive.call_llm_json", _mock_plan_response)
    secret_task = "Confidential: merger with Acme Corp, do not leak, code name Project Falcon."

    executive.plan(secret_task)

    event = audit.recent()[0]
    assert "Project Falcon" not in str(event)
    assert "Confidential" not in str(event)


def test_plan_records_fallback_status_when_llm_response_is_unusable(monkeypatch, tmp_path):
    audit = AuditStore(db_path=tmp_path / "audit.sqlite3")
    executive = make_executive(tmp_path, audit)
    monkeypatch.setattr("backend.ai_executive.call_llm_json", lambda *a, **k: {"_parse_error": True})

    executive.plan("task")

    event = audit.recent()[0]
    assert event.status == "fallback"


def test_dispatch_records_plan_then_dispatch_events_sharing_request_id(monkeypatch, tmp_path):
    audit = AuditStore(db_path=tmp_path / "audit.sqlite3")
    executive = make_executive(tmp_path, audit)
    monkeypatch.setattr("backend.ai_executive.call_llm_json", _mock_plan_response)

    result = executive.dispatch("A client task about inventory.")
    request_id = result.workflow_result["request_id"]

    events = audit.by_request(request_id)
    actions = sorted(e.action for e in events)
    assert actions == ["dispatch", "plan"]
    dispatch_event = [e for e in events if e.action == "dispatch"][0]
    assert dispatch_event.status == "complete"
    assert dispatch_event.decision in ("approve", "rework", None)


def test_dispatch_still_completes_when_audit_store_unavailable(monkeypatch, tmp_path):
    audit = broken_audit_store(tmp_path)
    executive = make_executive(tmp_path, audit)
    monkeypatch.setattr("backend.ai_executive.call_llm_json", _mock_plan_response)

    result = executive.dispatch("A task despite a broken audit backend.")

    assert result.workflow_result["status"] == "complete"
    assert result.workflow_result.get("final_report")


def test_workflow_state_still_untouched_by_audit_wiring(monkeypatch, tmp_path):
    audit = AuditStore(db_path=tmp_path / "audit.sqlite3")
    executive = make_executive(tmp_path, audit)
    monkeypatch.setattr("backend.ai_executive.call_llm_json", _mock_plan_response)

    from backend.ai_executive import workflow_graph
    captured_state = {}
    real_invoke = workflow_graph.invoke

    def spy_invoke(initial_state):
        captured_state.update(initial_state)
        return real_invoke(initial_state)

    monkeypatch.setattr("backend.ai_executive.workflow_graph.invoke", spy_invoke)

    executive.dispatch("A task.")

    assert set(captured_state.keys()) == {"request_id", "raw_request", "status", "log"}
