"""
Persistent workflow-run storage: RunStore unit tests, and restart/reload
behaviour of the API runtime (history, results, metrics, agent stats survive;
runs cut off by a restart are marked failed; storage failure degrades to
memory-only without breaking the API).
"""
import json
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import audit, memory, run_store as run_store_module
from backend.ai_executive import AIExecutive
from backend.api_adapter import AdapterRuntime, build_router
from backend.audit import AuditStore
from backend.knowledge_base import KnowledgeBase
from backend.memory import MemoryStore
from backend.run_store import RunStore

OBJECTIVE = "An organisation wants a technology recommendation for modernising a core process."


@pytest.fixture(autouse=True)
def mock_mode(monkeypatch):
    monkeypatch.setenv("MOCK_MODE", "1")


def make_executive(tmp_path, tag="") -> AIExecutive:
    return AIExecutive(
        memory=MemoryStore(db_path=tmp_path / f"mem{tag}.sqlite3"),
        knowledge_base=KnowledgeBase(db_path=tmp_path / f"kb{tag}.sqlite3"),
        audit_store=AuditStore(db_path=tmp_path / f"audit{tag}.sqlite3"),
    )


def boot(tmp_path, store, *, tag="", spawn=lambda fn: fn()):
    """Simulate (re)starting the service: a fresh executive and a fresh runtime
    over the same RunStore file."""
    executive = make_executive(tmp_path, tag)
    runtime = AdapterRuntime(executive, spawn=spawn, run_store=store)
    app = FastAPI()
    app.include_router(build_router(runtime))
    return TestClient(app), runtime, executive


def submit(client, objective=OBJECTIVE, **extra) -> str:
    response = client.post("/api/workflows", json={"objective": objective, **extra})
    assert response.status_code == 200
    return response.json()["request_id"]


def minimal_run(request_id="r1", **overrides) -> dict:
    run = {
        "request_id": request_id, "status": "completed", "user_request": "do a thing",
        "_task": "do a thing\n\nConstraints: none", "priority": "high", "department": "research",
        "submitted_by": "operator", "created_at": "2026-09-27T10:00:00+00:00",
        "started_at": "2026-09-27T10:00:01+00:00", "completed_at": "2026-09-27T10:00:05+00:00",
        "duration_ms": 4000, "current_stage": "", "request_type": "technology_recommendation",
        "engine": "ollama", "stages": [{"stage_id": "a", "status": "completed"}],
        "events": [{"id": 1, "message": "hi"}], "tasks": [{"agent_id": "research"}, {"agent_id": "review"}],
        "assignments": [], "memory_context": [], "solution_drafts": [], "review_history": [],
        "agent_runs": [], "error": "", "state": {"final_report": "# Report"},
        "_t0": 123.4, "_stage_t0": {"a": 1.0},
    }
    run.update(overrides)
    return run


# --- RunStore ---------------------------------------------------------------

def test_save_and_get_roundtrip_preserves_run_fields(tmp_path):
    store = RunStore(db_path=tmp_path / "runs.sqlite3")
    assert store.save(minimal_run()) is True

    run = store.get("r1")

    assert run["status"] == "completed" and run["priority"] == "high"
    assert run["user_request"] == "do a thing" and run["department"] == "research"
    assert run["submitted_by"] == "operator" and run["engine"] == "ollama"
    assert run["created_at"] == "2026-09-27T10:00:00+00:00" and run["duration_ms"] == 4000
    assert run["events"] == [{"id": 1, "message": "hi"}]
    assert run["state"] == {"final_report": "# Report"}
    assert run["_task"] == "do a thing\n\nConstraints: none"


def test_process_local_working_state_is_not_persisted(tmp_path):
    store = RunStore(db_path=tmp_path / "runs.sqlite3")
    store.save(minimal_run())
    raw = sqlite3.connect(tmp_path / "runs.sqlite3").execute("SELECT data FROM runs").fetchone()[0]
    assert "_t0" not in raw and "_stage_t0" not in raw

    run = store.get("r1")
    assert run["_t0"] is None and run["_stage_t0"] == {}


def test_save_upserts_instead_of_duplicating(tmp_path):
    store = RunStore(db_path=tmp_path / "runs.sqlite3")
    store.save(minimal_run(status="running", completed_at=None, progress=40.0))
    store.save(minimal_run(status="completed", progress=100.0))

    assert len(store) == 1
    assert store.get("r1")["status"] == "completed"
    assert store.get("r1")["progress"] == 100.0


def test_selected_agents_progress_and_failure_are_queryable_columns(tmp_path):
    store = RunStore(db_path=tmp_path / "runs.sqlite3")
    store.save(minimal_run(status="failed", error="The workflow stopped unexpectedly (X).", progress=42.5))

    row = sqlite3.connect(tmp_path / "runs.sqlite3").execute(
        "SELECT status, selected_agents, progress, error, task FROM runs").fetchone()
    assert row[0] == "failed"
    assert json.loads(row[1]) == ["research", "review"]
    assert row[2] == 42.5
    assert row[3] == "The workflow stopped unexpectedly (X)."
    assert row[4].startswith("do a thing")


def test_load_all_is_oldest_first_and_stable_across_updates(tmp_path):
    store = RunStore(db_path=tmp_path / "runs.sqlite3")
    for rid in ("a", "b", "c"):
        store.save(minimal_run(rid))
    store.save(minimal_run("a", status="failed"))  # updating must not reorder

    assert [r["request_id"] for r in store.load_all()] == ["a", "b", "c"]


def test_data_survives_a_new_store_instance_on_the_same_file(tmp_path):
    path = tmp_path / "runs.sqlite3"
    RunStore(db_path=path).save(minimal_run())
    reopened = RunStore(db_path=path)
    assert reopened.get("r1")["state"]["final_report"] == "# Report"
    assert len(reopened) == 1


def test_get_missing_returns_none(tmp_path):
    assert RunStore(db_path=tmp_path / "runs.sqlite3").get("nope") is None


def test_unserialisable_values_do_not_crash_save(tmp_path):
    store = RunStore(db_path=tmp_path / "runs.sqlite3")
    assert store.save(minimal_run(events=[{"id": 1, "message": object()}])) is True
    assert store.get("r1") is not None


def test_save_of_a_malformed_run_fails_safe(tmp_path):
    store = RunStore(db_path=tmp_path / "runs.sqlite3")
    with pytest.warns(RuntimeWarning, match="save failed"):
        assert store.save({"request_id": "x"}) is False


def test_unavailable_store_fails_safe(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    with pytest.warns(RuntimeWarning):
        store = RunStore(db_path=blocker / "sub" / "runs.sqlite3")
    assert store.is_available is False
    assert store.save(minimal_run()) is False
    assert store.load_all() == [] and store.get("r1") is None and len(store) == 0


def test_run_storage_is_a_separate_database_from_memory_and_audit():
    paths = {run_store_module.DEFAULT_DB_PATH, memory.DEFAULT_DB_PATH, audit.DEFAULT_DB_PATH}
    assert len(paths) == 3
    assert run_store_module.DEFAULT_DB_PATH.name == "runs.sqlite3"


# --- restart / reload of the API runtime ---------------------------------------

def test_workflow_history_and_results_survive_a_restart(tmp_path):
    store = RunStore(db_path=tmp_path / "runs.sqlite3")
    client, _, _ = boot(tmp_path, store, tag="1")
    first = submit(client, "First objective for the workforce to handle.", priority="high")
    second = submit(client, "Second objective for the workforce to handle.")
    before_list = client.get("/api/workflows").json()
    before_status = client.get(f"/api/workflows/{first}/status").json()
    before_result = client.get(f"/api/workflows/{first}/result").json()

    restarted, _, _ = boot(tmp_path, RunStore(db_path=tmp_path / "runs.sqlite3"), tag="2")

    after_list = restarted.get("/api/workflows").json()
    assert after_list == before_list
    assert [i["request_id"] for i in after_list["items"]] == [second, first]
    assert restarted.get(f"/api/workflows/{first}/status").json() == before_status
    assert restarted.get(f"/api/workflows/{first}/result").json() == before_result
    assert restarted.get(f"/api/workflows/{first}/status").json()["priority"] == "high"
    assert restarted.get(f"/api/workflows/{first}").json()["agent_runs"]


def test_metrics_dashboard_and_agent_stats_survive_a_restart(tmp_path):
    store = RunStore(db_path=tmp_path / "runs.sqlite3")
    client, _, _ = boot(tmp_path, store, tag="1")
    submit(client)
    submit(client, "Another objective for the workforce to work through.")
    before = {p: client.get(p).json() for p in ("/api/metrics", "/api/agents", "/api/agents/solution")}
    before_dashboard = client.get("/api/dashboard").json()

    restarted, _, _ = boot(tmp_path, RunStore(db_path=tmp_path / "runs.sqlite3"), tag="2")

    for path, expected in before.items():
        assert restarted.get(path).json() == expected, path
    dashboard = restarted.get("/api/dashboard").json()
    assert dashboard["summary"] == before_dashboard["summary"]
    assert dashboard["recent_workflows"] == before_dashboard["recent_workflows"]
    assert dashboard["throughput"] == before_dashboard["throughput"]
    assert dashboard["summary"]["completed_workflows"] == 2
    assert dashboard["summary"]["agent_runs"] == before_dashboard["summary"]["agent_runs"] > 0


def test_new_runs_after_a_restart_are_added_alongside_the_old_ones(tmp_path):
    store = RunStore(db_path=tmp_path / "runs.sqlite3")
    client, _, _ = boot(tmp_path, store, tag="1")
    old = submit(client)

    restarted, _, _ = boot(tmp_path, RunStore(db_path=tmp_path / "runs.sqlite3"), tag="2")
    new = submit(restarted, "A brand new objective submitted after the restart.")

    listed = [i["request_id"] for i in restarted.get("/api/workflows").json()["items"]]
    assert listed == [new, old] and new != old
    assert restarted.get("/api/metrics").json()["summary"]["total_workflows"] == 2
    assert len(RunStore(db_path=tmp_path / "runs.sqlite3")) == 2


def test_completed_run_stores_result_selected_agents_and_progress(tmp_path):
    store = RunStore(db_path=tmp_path / "runs.sqlite3")
    client, _, _ = boot(tmp_path, store)
    request_id = submit(client)

    stored = store.get(request_id)
    assert stored["status"] == "completed" and stored["progress"] == 100.0
    assert stored["state"]["final_report"].startswith("# ")
    assert stored["duration_ms"] is not None and stored["completed_at"]
    assert {t["agent_id"] for t in stored["tasks"]} == {
        "orchestrator", "research", "requirements", "analysis", "solution", "review", "report"}
    raw = sqlite3.connect(tmp_path / "runs.sqlite3").execute(
        "SELECT selected_agents, progress FROM runs").fetchone()
    assert len(json.loads(raw[0])) == 7 and raw[1] == 100.0


def test_run_detail_survives_reload(tmp_path):
    store = RunStore(db_path=tmp_path / "runs.sqlite3")
    client, _, _ = boot(tmp_path, store, tag="1")
    request_id = submit(client)

    restarted, _, _ = boot(tmp_path, RunStore(db_path=tmp_path / "runs.sqlite3"), tag="2")
    result = restarted.get(f"/api/workflows/{request_id}/result").json()

    assert len(result["solution_drafts"]) == 2
    assert [r["decision"] for r in result["review_history"]] == ["rework", "approve"]
    assert result["assignments"] and result["tasks"]
    status = restarted.get(f"/api/workflows/{request_id}/status").json()
    assert [s["status"] for s in status["stages"]] == ["completed"] * 7
    later = restarted.get(f"/api/workflows/{request_id}/status?after_event=5").json()
    assert all(e["id"] > 5 for e in later["events"]) and later["events"]


def test_failure_state_is_persisted_and_survives_a_restart(tmp_path, monkeypatch):
    store = RunStore(db_path=tmp_path / "runs.sqlite3")
    client, _, executive = boot(tmp_path, store, tag="1")
    monkeypatch.setattr(executive, "dispatch", lambda task, **k: (_ for _ in ()).throw(RuntimeError("secret /path")))
    request_id = submit(client)
    assert store.get(request_id)["status"] == "failed"

    restarted, _, _ = boot(tmp_path, RunStore(db_path=tmp_path / "runs.sqlite3"), tag="2")
    status = restarted.get(f"/api/workflows/{request_id}/status").json()

    assert status["status"] == "failed"
    assert status["error"] == "The workflow stopped unexpectedly (RuntimeError)."
    assert "secret" not in json.dumps(status)
    assert restarted.get("/api/metrics").json()["summary"]["failed_workflows"] == 1
    assert restarted.get(f"/api/workflows/{request_id}/result").status_code == 409


def test_progress_is_persisted_while_a_run_is_still_executing(tmp_path, monkeypatch):
    store = RunStore(db_path=tmp_path / "runs.sqlite3")
    client, _, executive = boot(tmp_path, store)
    seen = {}

    def partial_dispatch(task, *, request_id, on_progress, **kwargs):
        on_progress("stage_started", {"node": "orchestrate"})
        on_progress("stage_completed", {"node": "orchestrate", "state": {"request_type": "x", "log": []}})
        on_progress("stage_started", {"node": "research"})
        seen["mid"] = store.get(request_id)  # what a crash right now would leave behind
        return executive.__class__.dispatch(executive, task, request_id=request_id, on_progress=on_progress, **kwargs)

    monkeypatch.setattr(executive, "dispatch", partial_dispatch)
    submit(client)

    mid = seen["mid"]
    assert mid["status"] == "running" and mid["current_stage"] == "research"
    assert 0 < mid["progress"] < 100
    assert [s["status"] for s in mid["stages"]][:2] == ["completed", "running"]


def test_a_run_cut_off_by_a_restart_is_marked_failed_not_left_running(tmp_path):
    store = RunStore(db_path=tmp_path / "runs.sqlite3")
    client, _, _ = boot(tmp_path, store, tag="1", spawn=lambda fn: None)  # never starts
    request_id = submit(client)
    assert store.get(request_id)["status"] == "queued"

    restarted, _, executive = boot(tmp_path, RunStore(db_path=tmp_path / "runs.sqlite3"), tag="2")
    status = restarted.get(f"/api/workflows/{request_id}/status").json()

    assert status["status"] == "failed"
    assert "restart" in status["error"]
    assert {s["status"] for s in status["stages"]} == {"skipped"}
    assert restarted.get("/api/dashboard").json()["summary"]["active_workflows"] == 0
    assert restarted.get("/api/metrics").json()["summary"]["failed_workflows"] == 1
    event = executive.audit_store.by_request(request_id)[0]
    assert event.action == "dispatch" and event.status == "failed"
    assert event.metadata == {"error_type": "Interrupted"}


def test_interruption_is_persisted_so_it_is_only_recorded_once(tmp_path):
    store = RunStore(db_path=tmp_path / "runs.sqlite3")
    client, _, _ = boot(tmp_path, store, tag="1", spawn=lambda fn: None)
    request_id = submit(client)

    _, _, second = boot(tmp_path, RunStore(db_path=tmp_path / "runs.sqlite3"), tag="2")
    _, _, third = boot(tmp_path, RunStore(db_path=tmp_path / "runs.sqlite3"), tag="3")

    assert len(second.audit_store.by_request(request_id)) == 1
    assert third.audit_store.by_request(request_id) == []  # already failed on disk; not re-interrupted
    assert RunStore(db_path=tmp_path / "runs.sqlite3").get(request_id)["status"] == "failed"


def test_a_running_run_at_restart_marks_its_running_stage_failed(tmp_path):
    store = RunStore(db_path=tmp_path / "runs.sqlite3")
    run = minimal_run("mid", status="running", completed_at=None, duration_ms=None, current_stage="research",
                      stages=[{"stage_id": "orchestrate", "label": "Orchestration", "agent_id": "orchestrator",
                               "department": "", "status": "completed", "attempt": 1, "started_at": None,
                               "completed_at": None, "duration_ms": 5, "detail": ""},
                              {"stage_id": "research", "label": "Research", "agent_id": "research",
                               "department": "", "status": "running", "attempt": 1, "started_at": None,
                               "completed_at": None, "duration_ms": None, "detail": ""},
                              {"stage_id": "report", "label": "Final Report", "agent_id": "report",
                               "department": "", "status": "pending", "attempt": 0, "started_at": None,
                               "completed_at": None, "duration_ms": None, "detail": ""}],
                      state={})
    store.save(run)

    client, _, _ = boot(tmp_path, RunStore(db_path=tmp_path / "runs.sqlite3"))
    stages = {s["stage_id"]: s["status"] for s in client.get("/api/workflows/mid/status").json()["stages"]}

    assert stages == {"orchestrate": "completed", "research": "failed", "report": "skipped"}


def test_runtime_without_a_store_is_memory_only(tmp_path):
    executive = make_executive(tmp_path)
    runtime = AdapterRuntime(executive, spawn=lambda fn: fn())
    app = FastAPI()
    app.include_router(build_router(runtime))
    client = TestClient(app)
    submit(client)
    assert client.get("/api/workflows").json()["total"] == 1
    assert client.get("/health").json()["database"] == "ok"

    fresh = AdapterRuntime(make_executive(tmp_path, "b"), spawn=lambda fn: fn())
    assert fresh.list_payload(10, 0, None)["total"] == 0


def test_unavailable_run_store_degrades_to_memory_only_without_breaking_the_api(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    with pytest.warns(RuntimeWarning):
        broken = RunStore(db_path=blocker / "sub" / "runs.sqlite3")
    client, _, _ = boot(tmp_path, broken)

    request_id = submit(client)

    assert client.get(f"/api/workflows/{request_id}/status").json()["status"] == "completed"
    assert client.get("/api/workflows").json()["total"] == 1
    health = client.get("/health").json()
    assert health["database"] == "unavailable" and health["status"] == "degraded"


def test_unreadable_history_does_not_stop_the_service_from_starting(tmp_path):
    path = tmp_path / "runs.sqlite3"
    store = RunStore(db_path=path)
    store.save(minimal_run())
    conn = sqlite3.connect(path)
    conn.execute("UPDATE runs SET data = 'not json'")
    conn.commit()
    conn.close()

    with pytest.warns(RuntimeWarning, match="load_all failed"):
        client, _, _ = boot(tmp_path, RunStore(db_path=path))

    assert client.get("/api/workflows").json()["total"] == 0
    assert submit(client)  # and it still works


def test_the_main_app_runtime_is_backed_by_a_run_store():
    import backend.main  # noqa: F401  (ensures the default runtime exists)
    from backend import api_adapter
    assert isinstance(api_adapter.default_run_store, RunStore)
