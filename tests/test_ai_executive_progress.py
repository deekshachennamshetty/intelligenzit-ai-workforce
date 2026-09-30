"""
Tests for AIExecutive.dispatch(on_progress=...): the optional, purely
observational progress hook the HTTP adapter uses for live stage updates.
It must not change what the workflow does or returns.
"""
import itertools

import pytest

from backend import llm
from backend.ai_executive import AIExecutive
from backend.audit import AuditStore
from backend.knowledge_base import KnowledgeBase
from backend.memory import MemoryStore

TASK = "A client wants a technology recommendation for modernising an internal system."
NODES = ["orchestrate", "research", "requirements", "analysis", "solution", "review", "solution", "review", "report"]


@pytest.fixture(autouse=True)
def mock_mode(monkeypatch):
    monkeypatch.setenv("MOCK_MODE", "1")


def make_executive(tmp_path) -> AIExecutive:
    return AIExecutive(
        memory=MemoryStore(db_path=tmp_path / "mem.sqlite3"),
        knowledge_base=KnowledgeBase(db_path=tmp_path / "kb.sqlite3"),
        audit_store=AuditStore(db_path=tmp_path / "audit.sqlite3"),
    )


def test_progress_events_cover_plan_then_every_stage_start_and_completion(tmp_path):
    events = []
    make_executive(tmp_path).dispatch(TASK, on_progress=lambda kind, payload: events.append((kind, payload)))

    assert events[0][0] == "plan" and events[0][1]["plan"].steps
    stage_events = [(k, p["node"]) for k, p in events[1:]]
    started = [n for k, n in stage_events if k == "stage_started"]
    completed = [n for k, n in stage_events if k == "stage_completed"]
    assert started == NODES and completed == NODES
    # every stage starts before it completes, and the next one only starts after
    for index in range(len(NODES)):
        assert stage_events.index(("stage_started", NODES[index])) <= stage_events.index(("stage_completed", NODES[index]))


def test_stage_completed_carries_the_updated_state(tmp_path):
    seen = {}

    def on_progress(kind, payload):
        if kind == "stage_completed":
            seen.setdefault(payload["node"], payload["state"])

    make_executive(tmp_path).dispatch(TASK, on_progress=on_progress)

    assert seen["research"].get("research_findings")
    assert seen["solution"].get("solution")
    assert seen["report"].get("final_report") and seen["report"]["status"] == "complete"


def test_result_is_identical_with_and_without_a_progress_callback(tmp_path):
    plain = make_executive(tmp_path).dispatch(TASK, request_id="same")
    llm._review_call_count = itertools.count(1)  # fresh reject-then-approve cycle
    observed = make_executive(tmp_path).dispatch(TASK, request_id="same", on_progress=lambda *a: None)

    keys = ["status", "final_report", "review_decision", "request_type", "solution", "requirements"]
    assert {k: plain.workflow_result.get(k) for k in keys} == {k: observed.workflow_result.get(k) for k in keys}
    assert [e["stage"] for e in plain.workflow_result["log"]] == [e["stage"] for e in observed.workflow_result["log"]]
    assert plain.plan.agent_ids == observed.plan.agent_ids


def test_without_a_callback_the_graph_still_runs_via_invoke(tmp_path, monkeypatch):
    executive = make_executive(tmp_path)
    from backend.ai_executive import workflow_graph
    calls = []
    real_invoke = workflow_graph.invoke
    monkeypatch.setattr(workflow_graph, "invoke", lambda s: calls.append(1) or real_invoke(s))

    executive.dispatch(TASK)

    assert calls == [1]


def test_with_a_callback_the_graph_is_streamed_not_invoked(tmp_path, monkeypatch):
    executive = make_executive(tmp_path)
    from backend.ai_executive import workflow_graph

    def forbidden(_state):
        raise AssertionError("invoke() must not be used when observing progress")

    monkeypatch.setattr(workflow_graph, "invoke", forbidden)

    result = executive.dispatch(TASK, on_progress=lambda *a: None)

    assert result.workflow_result["status"] == "complete"


def test_a_failing_callback_never_breaks_the_run(tmp_path):
    def broken(kind, payload):
        raise RuntimeError("listener bug")

    with pytest.warns(RuntimeWarning, match="progress callback failed"):
        result = make_executive(tmp_path).dispatch(TASK, on_progress=broken)

    assert result.workflow_result["status"] == "complete"
    assert result.workflow_result["final_report"]


def test_audit_and_memory_hooks_still_fire_when_observed(tmp_path):
    executive = make_executive(tmp_path)
    result = executive.dispatch(TASK, request_id="obs-1", on_progress=lambda *a: None)

    assert sorted(e.action for e in executive.audit_store.by_request("obs-1")) == ["dispatch", "plan"]
    assert executive.memory.count() == 1
    assert result.plan.fallback_used is True  # mock LLM gives no usable plan -> deterministic fallback
