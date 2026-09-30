"""
Tests for backend/api_adapter.py — every /api/* route the operator frontend
depends on, plus /health, static serving, execution via AIExecutive, failure
handling, secret hygiene, and the legacy routes staying intact.

Workflows run in MOCK_MODE with a synchronous spawner, so every test is
deterministic and offline.
"""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import api_adapter
from backend.agent_registry import AgentDefinition, AgentRegistry, AgentStatus, default_registry
from backend.ai_executive import AIExecutive
from backend.api_adapter import AdapterRuntime, build_router, mount_frontend
from backend.audit import AuditStore
from backend.knowledge_base import KnowledgeBase
from backend.memory import MemoryStore
from backend.tool_registry import CALCULATOR_TOOL, ToolRegistry, assign_agent_tools

OBJECTIVE = "An organisation wants to modernise a core process and needs a technology recommendation."


@pytest.fixture(autouse=True)
def mock_mode(monkeypatch):
    monkeypatch.setenv("MOCK_MODE", "1")


def make_executive(tmp_path, **overrides) -> AIExecutive:
    kwargs = dict(
        memory=MemoryStore(db_path=tmp_path / "mem.sqlite3"),
        knowledge_base=KnowledgeBase(db_path=tmp_path / "kb.sqlite3"),
        audit_store=AuditStore(db_path=tmp_path / "audit.sqlite3"),
    )
    kwargs.update(overrides)
    return AIExecutive(**kwargs)


def make_client(executive, *, spawn=lambda fn: fn()):
    runtime = AdapterRuntime(executive, spawn=spawn)
    app = FastAPI()
    app.include_router(build_router(runtime))
    return TestClient(app), runtime


@pytest.fixture
def stack(tmp_path):
    executive = make_executive(tmp_path)
    client, runtime = make_client(executive)
    return client, executive, runtime


def submit(client, objective=OBJECTIVE, **extra) -> str:
    response = client.post("/api/workflows", json={"objective": objective, **extra})
    assert response.status_code == 200
    return response.json()["request_id"]


# --- GET /health ------------------------------------------------------------

def test_health_reports_ok_in_mock_mode(stack):
    client, _, _ = stack
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["llm"]["active_engine"] == "mock"
    assert body["agents_registered"] == 7
    assert body["product"] and body["version"]


def test_health_degraded_when_provider_unreachable(stack, monkeypatch):
    client, _, _ = stack
    monkeypatch.delenv("MOCK_MODE")
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setattr(api_adapter, "_provider_reachable", lambda provider: False)
    body = client.get("/health").json()
    assert body["status"] == "degraded"
    assert body["llm"]["reachable"] is False
    assert body["llm"]["provider"] == "ollama"


def test_health_ok_when_ollama_reachable(stack, monkeypatch):
    client, _, _ = stack
    monkeypatch.delenv("MOCK_MODE")
    monkeypatch.setattr(api_adapter, "_provider_reachable", lambda provider: True)
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["llm"]["active_engine"] == "ollama"


def test_health_degraded_when_a_store_is_unavailable(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    with pytest.warns(RuntimeWarning):
        broken = AuditStore(db_path=blocker / "sub" / "audit.sqlite3")
    client, _ = make_client(make_executive(tmp_path, audit_store=broken))
    body = client.get("/health").json()
    assert body["database"] == "unavailable"
    assert body["status"] == "degraded"


# --- GET /api/config and /api/graph --------------------------------------------

def test_config_lists_the_seven_pipeline_stages(stack):
    client, _, _ = stack
    body = client.get("/api/config").json()
    assert [s["id"] for s in body["stages"]] == [
        "orchestrate", "research", "requirements", "analysis", "solution", "review", "report"]
    assert all({"id", "label", "agent"} <= set(s) for s in body["stages"])
    assert body["max_rework_cycles"] == 2
    assert body["api_key_required"] is False
    assert body["require_human_approval"] is False
    assert body["graph"]["entry"] == "orchestrate"
    assert "quality_threshold" not in body  # no numeric quality gate exists in this backend


def test_graph_has_a_conditional_rework_edge(stack):
    client, _, _ = stack
    body = client.get("/api/graph").json()
    conditions = {(e["from"], e["to"]): e.get("condition") for e in body["edges"]}
    assert conditions[("review", "solution")] == "rework"
    assert conditions[("review", "report")] == "approve"
    assert {n["id"] for n in body["nodes"]} == {s["id"] for s in client.get("/api/config").json()["stages"]}


# --- POST /api/workflows (execution goes through AIExecutive) -----------------------

def test_create_workflow_returns_request_id_and_runs_to_completion(stack):
    client, _, _ = stack
    request_id = submit(client, priority="high", expected_outcome="A roadmap", constraints="Two quarters")
    status = client.get(f"/api/workflows/{request_id}/status").json()
    assert status["status"] == "completed"
    assert status["priority"] == "high"
    assert status["objective"] == OBJECTIVE


def test_create_workflow_executes_via_ai_executive_not_the_graph_directly(tmp_path, monkeypatch):
    executive = make_executive(tmp_path)
    calls = []
    real_dispatch = executive.dispatch

    def spy_dispatch(task, **kwargs):
        calls.append((task, kwargs))
        return real_dispatch(task, **kwargs)

    monkeypatch.setattr(executive, "dispatch", spy_dispatch)
    client, _ = make_client(executive)
    request_id = submit(client, expected_outcome="A roadmap", constraints="Two quarters")

    assert len(calls) == 1
    task, kwargs = calls[0]
    assert task.startswith(OBJECTIVE)
    assert "Expected outcome: A roadmap" in task and "Constraints: Two quarters" in task
    assert kwargs["request_id"] == request_id and callable(kwargs["on_progress"])
    # AIExecutive's own audit hooks fired for this request => it really ran through the Executive.
    actions = sorted(e.action for e in executive.audit_store.by_request(request_id))
    assert actions == ["dispatch", "plan"]


@pytest.mark.parametrize("payload", [
    {"objective": ""},
    {"priority": "high"},
    {"objective": OBJECTIVE, "priority": "urgent-ish"},
])
def test_create_workflow_rejects_invalid_bodies(stack, payload):
    client, _, _ = stack
    assert client.post("/api/workflows", json=payload).status_code == 422


# --- GET /api/workflows (list) ---------------------------------------------------------

def test_list_workflows_is_newest_first_and_filterable(tmp_path):
    executive = make_executive(tmp_path)
    pending = []
    client, runtime = make_client(executive, spawn=pending.append)
    first = submit(client, "First objective for the workforce to handle.")
    second = submit(client, "Second objective for the workforce to handle.")
    pending[0]()  # only the first one runs

    body = client.get("/api/workflows").json()
    assert body["total"] == 2
    assert [i["request_id"] for i in body["items"]] == [second, first]
    assert client.get("/api/workflows?status=completed").json()["total"] == 1
    assert client.get("/api/workflows?status=queued").json()["items"][0]["request_id"] == second
    assert client.get("/api/workflows?limit=1&offset=1").json()["items"][0]["request_id"] == first
    row = body["items"][1]
    assert {"request_id", "user_request", "status", "created_at", "duration_ms",
            "rework_count"} <= set(row)
    assert "quality_score" not in row  # the current backend never computes one — no fake value


# --- GET /api/workflows/{id} (detail) ----------------------------------------------------

def test_workflow_detail_includes_summary_agent_runs_and_result(stack):
    client, _, _ = stack
    request_id = submit(client)
    body = client.get(f"/api/workflows/{request_id}").json()
    assert body["summary"]["status"] == "completed"
    nodes = [r["node"] for r in body["agent_runs"]]
    assert nodes == ["orchestrate", "research", "requirements", "analysis",
                     "solution", "review", "solution", "review", "report"]
    assert all(r["output"] is not None for r in body["agent_runs"])
    assert body["result"]["final_proposal"]


def test_workflow_detail_404_for_unknown_id(stack):
    client, _, _ = stack
    assert client.get("/api/workflows/nope").status_code == 404


# --- GET /api/workflows/{id}/status ---------------------------------------------------------

def test_status_reports_stage_progress_events_tasks_and_observed_rework(stack):
    client, _, _ = stack
    request_id = submit(client)
    status = client.get(f"/api/workflows/{request_id}/status").json()

    assert status["progress"] == 100.0
    assert [s["status"] for s in status["stages"]] == ["completed"] * 7
    by_id = {s["stage_id"]: s for s in status["stages"]}
    assert by_id["solution"]["attempt"] == 2 and by_id["review"]["attempt"] == 2
    assert by_id["orchestrate"]["duration_ms"] is not None
    assert status["rework_count"] == 1  # mock reviewer rejects once, then approves
    assert status["review_decision"] == "approve"
    assert status["request_type"] == "technology_recommendation"
    assert len(status["tasks"]) == 7 and all(t["status"] == "completed" for t in status["tasks"])
    ids = [e["id"] for e in status["events"]]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)
    assert any(e["level"] == "warn" for e in status["events"])  # the rework hand-back

    later = client.get(f"/api/workflows/{request_id}/status?after_event={ids[3]}").json()
    assert [e["id"] for e in later["events"]] == [i for i in ids if i > ids[3]]


def test_status_404_for_unknown_id(stack):
    client, _, _ = stack
    assert client.get("/api/workflows/nope/status").status_code == 404


def test_status_of_a_queued_run_has_pending_stages(tmp_path):
    client, _ = make_client(make_executive(tmp_path), spawn=lambda fn: None)
    request_id = submit(client)
    status = client.get(f"/api/workflows/{request_id}/status").json()
    assert status["status"] == "queued" and status["progress"] == 0.0
    assert {s["status"] for s in status["stages"]} == {"pending"}


# --- GET /api/workflows/{id}/result -----------------------------------------------------------

def test_result_maps_workflow_state_into_the_deliverable_shape(stack):
    client, _, _ = stack
    request_id = submit(client)
    result = client.get(f"/api/workflows/{request_id}/result").json()

    assert result["status"] == "completed"
    assert result["final_proposal"].startswith("# ")
    assert "MOCK RESEARCH FINDINGS" in result["research"]
    assert "MOCK REQUIREMENTS" in result["data_requirements"]
    assert "MOCK ANALYSIS" in result["analysis"]
    assert "MOCK SOLUTION" in result["strategy"]
    assert len(result["solution_drafts"]) == 2
    assert [r["decision"] for r in result["review_history"]] == ["rework", "approve"]
    assert result["review_history"][0]["rework_target"] == "solution"
    assert result["executive_decision"]["decision"] == "approved"
    assert result["assignments"] and result["tasks"]
    # No fabricated quality score, and no fields the backend never populates.
    for absent in ("quality_score", "risks", "architecture", "objective_analysis",
                   "report_sections", "engine_breakdown", "goals", "metrics", "errors", "review"):
        assert absent not in result
    for entry in result["review_history"]:
        assert "quality_score" not in entry


def test_result_is_409_until_the_run_completes(tmp_path):
    client, _ = make_client(make_executive(tmp_path), spawn=lambda fn: None)
    request_id = submit(client)
    response = client.get(f"/api/workflows/{request_id}/result")
    assert response.status_code == 409
    assert "queued" in response.json()["detail"]


def test_result_404_for_unknown_id(stack):
    client, _, _ = stack
    assert client.get("/api/workflows/nope/result").status_code == 404


# --- POST /api/workflows/{id}/decision ----------------------------------------------------------------

def test_decision_is_refused_because_no_human_gate_exists(stack):
    client, _, _ = stack
    request_id = submit(client)
    response = client.post(f"/api/workflows/{request_id}/decision", json={"decision": "approve"})
    assert response.status_code == 409
    assert "not enabled" in response.json()["detail"]


def test_decision_404_for_unknown_id(stack):
    client, _, _ = stack
    assert client.post("/api/workflows/nope/decision", json={"decision": "approve"}).status_code == 404


# --- GET /api/workflows/{id}/research and GET /api/research ----------------------------------------------

def test_workflow_research_reports_no_structured_dossier_honestly(stack):
    client, _, _ = stack
    request_id = submit(client)
    body = client.get(f"/api/workflows/{request_id}/research").json()
    assert body["available"] is False
    assert "narrative" in body["reason"]
    assert body["stage_status"] == "completed"
    assert body["objective"] == OBJECTIVE
    assert any("Research completed" in t["label"] for t in body["timeline"])


def test_workflow_research_404_for_unknown_id(stack):
    client, _, _ = stack
    assert client.get("/api/workflows/nope/research").status_code == 404


def test_research_index_is_empty_with_a_disclosure(stack):
    client, _, _ = stack
    body = client.get("/api/research").json()
    assert body["items"] == [] and body["stats"]["has_data"] is False
    assert "no live web retrieval" in body["disclosure"].lower()


# --- GET /api/agents and /api/agents/{id} ---------------------------------------------------------------------

def test_agents_lists_the_registry_grouped_by_department(stack):
    client, _, _ = stack
    body = client.get("/api/agents").json()
    listed = {a["id"] for d in body["departments"] for a in d["agents"]}
    assert listed == {a.agent_id for a in default_registry.list_agents()}
    assert body["totals"]["agents"] == 7 and body["totals"]["executing"] == 7
    agent = body["departments"][0]["agents"][0]
    assert {"id", "name", "title", "department", "capabilities", "permissions", "executes",
            "seniority", "mission", "status", "runs", "reliability"} <= set(agent)
    assert all(d["mandate"] and d["name"] for d in body["departments"])


def test_agents_reflect_live_activity_after_a_run(stack):
    client, _, _ = stack
    submit(client)
    agents = {a["id"]: a for d in client.get("/api/agents").json()["departments"] for a in d["agents"]}
    assert agents["solution"]["runs"] == 2 and agents["review"]["runs"] == 2
    assert agents["orchestrator"]["runs"] == 1 and agents["orchestrator"]["reliability"] == 100
    assert agents["report"]["last_active"]


def test_agent_detail_returns_profile_history_and_peers(stack):
    client, _, _ = stack
    request_id = submit(client)
    body = client.get("/api/agents/solution").json()
    assert body["agent"]["id"] == "solution" and body["agent"]["department_name"]
    assert body["stats"]["runs"] == 2
    assert len(body["history"]) == 2 and body["history"][0]["request_id"] == request_id
    assert isinstance(body["peers"], list)


def test_agent_detail_404_for_unknown_agent(stack):
    client, _, _ = stack
    assert client.get("/api/agents/nobody").status_code == 404


# --- GET /api/memory ----------------------------------------------------------------------------------------------

def test_memory_starts_empty(stack):
    client, _, _ = stack
    assert client.get("/api/memory").json() == {"total": 0, "by_kind": {}, "entries": []}


def test_memory_lists_what_the_run_persisted(stack):
    client, executive, _ = stack
    request_id = submit(client)
    body = client.get("/api/memory").json()
    assert body["total"] == 1 and body["by_kind"] == {"deliverable": 1}
    entry = body["entries"][0]
    assert entry["request_id"] == request_id
    assert entry["content"].startswith("# ")
    assert entry["kind"] == "deliverable"


def test_memory_recall_and_kind_filter(stack):
    client, _, _ = stack
    submit(client)
    recalled = client.get("/api/memory", params={"q": "modernise technology recommendation"}).json()
    assert recalled["query"] and len(recalled["entries"]) == 1
    assert 0 < recalled["entries"][0]["confidence"] <= 1
    assert client.get("/api/memory", params={"q": "zzzunmatchable"}).json()["entries"] == []
    assert client.get("/api/memory", params={"kind": "lesson"}).json()["entries"] == []


# --- GET /api/metrics and /api/dashboard ---------------------------------------------------------------------------

def test_metrics_are_empty_before_any_run_and_computed_after(stack):
    client, _, _ = stack
    before = client.get("/api/metrics").json()
    assert before["summary"]["has_data"] is False and before["stages"] == []

    submit(client)
    body = client.get("/api/metrics").json()
    summary = body["summary"]
    assert summary["total_workflows"] == 1 and summary["completed_workflows"] == 1
    assert summary["success_rate"] == 100.0 and summary["failed_workflows"] == 0
    assert summary["workflows_with_rework"] == 1 and summary["total_reworks"] == 1
    assert summary["agent_runs"] == 9 and summary["avg_duration_ms"] >= 0
    assert {s["node"] for s in body["stages"]} == {
        "orchestrate", "research", "requirements", "analysis", "solution", "review", "report"}
    assert body["throughput"][0]["completed"] == 1
    assert body["department_load"]
    assert "avg_quality_score" not in summary and "quality_distribution" not in body


def test_dashboard_rolls_up_recent_activity(stack):
    client, _, _ = stack
    submit(client)
    body = client.get("/api/dashboard").json()
    assert body["summary"]["completed_workflows"] == 1
    assert body["agents_engaged"] == 7 and body["agents_total"] == 7
    assert body["recent_workflows"][0]["status"] == "completed"
    assert body["recent_events"] and all(e["level"] != "debug" for e in body["recent_events"])
    assert body["engine"]["active_engine"] == "mock"
    assert body["throughput"] and body["departments"]
    assert body["active_workflows"] == 0


# --- GET /api/governance and /api/audit ---------------------------------------------------------------------------------

def test_governance_reflects_real_policy_permissions_and_audit(tmp_path):
    tools = ToolRegistry(audit_store=AuditStore(db_path=tmp_path / "t.sqlite3"))
    tools.register(CALCULATOR_TOOL)
    executive = make_executive(tmp_path, tool_registry=tools)
    client, _ = make_client(executive)
    submit(client)

    body = client.get("/api/governance").json()
    assert body["policy"]["max_rework_cycles"] == 2
    assert body["policy"]["api_key_required"] is False
    for absent in ("quality_threshold", "max_concurrent_workflows", "workflow_timeout_seconds"):
        assert absent not in body["policy"]
    controls = {c["control"]: c["status"] for c in body["security"]}
    assert controls["API authentication"] == "not_implemented"
    assert controls["Tool allowlist"] == "enforced"
    assert controls["Audit trail with redaction"] == "enforced"
    assert [c["id"] for c in body["permissions"]["catalogue"]] == ["tool:calculator"]
    assert len(body["permissions"]["matrix"]) == 7
    assert body["review_gates"][0]["owner"] == "review"
    assert {"action": "plan", "actor": "ai_executive"}.items() <= {
        "action": body["audit"][-1]["action"], "actor": body["audit"][-1]["actor"]}.items() \
        or any(e["action"] == "dispatch" and e["outcome"] == "ok" for e in body["audit"])
    assert set(body["containment"]) >= {"authorisation_denials", "contained_failures", "escalations"}
    assert body["provider"]["active_engine"] == "mock"


def test_governance_permission_matrix_shows_assigned_tools(tmp_path):
    executive = make_executive(tmp_path)
    registry = AgentRegistry()
    registry.register(AgentDefinition(agent_id="solution", name="Solution", department="D", role="R",
                                      description="d", status=AgentStatus.ACTIVE))
    tools = ToolRegistry(audit_store=executive.audit_store)
    tools.register(CALCULATOR_TOOL)
    assign_agent_tools(registry, "solution", ["calculator"], tool_registry=tools)
    executive.registry, executive.tool_registry = registry, tools
    client, _ = make_client(executive)

    matrix = client.get("/api/governance").json()["permissions"]["matrix"]
    assert matrix == [{"agent_id": "solution", "name": "Solution", "department_name": "D",
                       "executes": True, "permissions": ["tool:calculator"]}]


def test_audit_lists_recorded_events(stack):
    client, _, _ = stack
    request_id = submit(client)
    entries = client.get("/api/audit").json()["entries"]
    assert {e["action"] for e in entries} == {"plan", "dispatch"}
    dispatch = next(e for e in entries if e["action"] == "dispatch")
    assert dispatch["target"] == request_id and dispatch["outcome"] == "ok"
    assert len(client.get("/api/audit?limit=1").json()["entries"]) == 1


def test_audit_view_maps_tool_denials(tmp_path):
    executive = make_executive(tmp_path)
    executive.audit_store.append(actor="tool_registry", action="tool_execute",
                                 status="unknown_tool", tool_id="ghost")
    client, _ = make_client(executive)
    entry = client.get("/api/audit").json()["entries"][0]
    assert entry["outcome"] == "denied" and entry["target"] == "ghost"
    assert client.get("/api/governance").json()["containment"]["authorisation_denials"] == 1


# --- failure handling ---------------------------------------------------------------------------------------------------------

def test_a_failing_run_is_reported_safely_and_audited(tmp_path, monkeypatch):
    executive = make_executive(tmp_path)

    def boom(task, **kwargs):
        raise RuntimeError("secret internal detail /home/user/.env")

    monkeypatch.setattr(executive, "dispatch", boom)
    client, _ = make_client(executive)
    request_id = submit(client)

    status = client.get(f"/api/workflows/{request_id}/status").json()
    assert status["status"] == "failed"
    assert "secret internal detail" not in json.dumps(status)
    assert status["error"] == "The workflow stopped unexpectedly (RuntimeError)."
    assert {s["status"] for s in status["stages"]} == {"skipped"}
    assert client.get(f"/api/workflows/{request_id}/result").status_code == 409
    failed = executive.audit_store.by_request(request_id)[0]
    assert failed.action == "dispatch" and failed.status == "failed"
    assert client.get("/api/metrics").json()["summary"]["failed_workflows"] == 1


def test_connection_failures_get_a_helpful_generic_message(tmp_path, monkeypatch):
    executive = make_executive(tmp_path)
    monkeypatch.setattr(executive, "dispatch", lambda task, **k: (_ for _ in ()).throw(ConnectionError("x")))
    client, _ = make_client(executive)
    request_id = submit(client)
    assert client.get(f"/api/workflows/{request_id}/status").json()["error"] == \
        "The language model provider could not be reached."


def test_runs_still_complete_when_audit_and_memory_are_unavailable(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    with pytest.warns(RuntimeWarning):
        broken_audit = AuditStore(db_path=blocker / "a" / "audit.sqlite3")
        broken_memory = MemoryStore(db_path=blocker / "m" / "mem.sqlite3")
    client, _ = make_client(make_executive(tmp_path, audit_store=broken_audit, memory=broken_memory))
    request_id = submit(client)
    assert client.get(f"/api/workflows/{request_id}/status").json()["status"] == "completed"


# --- secret hygiene -----------------------------------------------------------------------------------------------------------------

def test_no_endpoint_exposes_credentials(tmp_path, monkeypatch):
    monkeypatch.delenv("MOCK_MODE")
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-super-secret-value")
    client, _ = make_client(make_executive(tmp_path))
    dumped = " ".join(client.get(p).text for p in
                      ["/health", "/api/config", "/api/governance", "/api/dashboard", "/api/audit"])
    assert "sk-ant-super-secret-value" not in dumped
    assert client.get("/health").json()["llm"]["credentials_configured"] is True


# --- static frontend ---------------------------------------------------------------------------------------------------------------------

@pytest.fixture
def frontend_client(tmp_path):
    root = tmp_path / "frontend"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<html>landing</html>")
    (root / "dashboard.html").write_text("<html>dashboard</html>")
    (root / "legacy-mvp.html").write_text("<html>legacy</html>")
    (root / "assets" / "app.css").write_text("body{}")
    (tmp_path / "secret.html").write_text("<html>outside</html>")
    app = FastAPI()
    mount_frontend(app, root)
    return TestClient(app)


def test_frontend_serves_index_pages_and_assets(frontend_client):
    assert "landing" in frontend_client.get("/").text
    assert "dashboard" in frontend_client.get("/dashboard.html").text
    assert "legacy" in frontend_client.get("/legacy-mvp.html").text
    assert frontend_client.get("/assets/app.css").text == "body{}"


@pytest.mark.parametrize("path", ["/missing.html", "/..%5Csecret.html", "/%2e%2e/secret.html", "/a.b.html"])
def test_frontend_does_not_serve_unknown_or_traversal_paths(frontend_client, path):
    assert frontend_client.get(path).status_code == 404


def test_mount_frontend_is_a_noop_without_an_assets_directory(tmp_path):
    app = FastAPI()
    mount_frontend(app, tmp_path / "nowhere")
    assert TestClient(app).get("/").status_code == 404


def test_real_frontend_directory_is_served_by_the_main_app():
    from backend.main import app
    client = TestClient(app)
    assert client.get("/").status_code == 200
    for page in ["dashboard", "agents", "workflow", "workflows", "new-workflow", "deliverable",
                 "research", "memory", "metrics", "governance", "settings", "legacy-mvp",
                 "login", "users"]:
        assert client.get(f"/{page}.html").status_code == 200, page
    assert client.get("/assets/api/client.js").status_code == 200
    assert client.get("/assets/css/tokens.css").status_code == 200


# --- main app: adapter mounted, legacy routes intact ---------------------------------------------------------------------------------------

def test_main_app_exposes_the_adapter_and_docs_at_api_docs(signed_in):
    from backend.main import app
    client = TestClient(app)
    assert client.get("/health").status_code == 200
    assert client.get("/api/config").status_code == 401  # protected without a session
    assert client.get("/api/config", headers=signed_in("viewer")).status_code == 200
    assert client.get("/api/docs").status_code == 200
    paths = client.get("/api/openapi.json").json()["paths"]
    for path in ["/api/workflows", "/api/workflows/{request_id}/status", "/api/agents", "/api/memory",
                 "/api/metrics", "/api/dashboard", "/api/governance", "/api/audit", "/api/research",
                 "/api/graph", "/health", "/submit", "/status/{request_id}", "/result/{request_id}",
                 "/history"]:
        assert path in paths, path


def test_legacy_routes_still_work(monkeypatch, signed_in):
    from backend import main

    class StubGraph:
        def invoke(self, state):
            return {**state, "status": "complete", "final_report": "legacy report", "log": []}

    monkeypatch.setattr(main, "workflow_graph", StubGraph())
    client = TestClient(main.app)
    auth = signed_in("operator")  # legacy routes keep their contract but now require a session

    request_id = client.post("/submit", json={"text": "a legacy submission"}, headers=auth).json()["request_id"]
    for _ in range(200):
        if client.get(f"/status/{request_id}", headers=auth).json()["status"] != "running":
            break
    assert client.get(f"/status/{request_id}", headers=auth).json()["status"] == "complete"
    assert client.get(f"/result/{request_id}", headers=auth).json()["final_report"] == "legacy report"
    assert any(item["request_id"] == request_id for item in client.get("/history", headers=auth).json())
    assert client.get("/status/unknown", headers=auth).status_code == 404
    assert client.get("/result/unknown", headers=auth).status_code == 404
