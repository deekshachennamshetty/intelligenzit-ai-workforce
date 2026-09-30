"""
Tests for the memory integration in backend/ai_executive.py: retrieval
before planning, persistence after dispatch, the full store-then-retrieve
loop across separate AIExecutive instances (simulating reuse across
requests/restarts), and fail-safe behavior when memory is unavailable.
"""
import pytest

from backend.agent_registry import default_registry
from backend.ai_executive import AIExecutive, DEFAULT_PIPELINE_ORDER
from backend.memory import MemoryStore


@pytest.fixture(autouse=True)
def mock_mode(monkeypatch):
    monkeypatch.setenv("MOCK_MODE", "1")


def _mock_plan_response(system, user, **kwargs):
    return {
        "task_type": "technology_recommendation",
        "agents": [{"agent_id": a, "reason": "part of pipeline"} for a in DEFAULT_PIPELINE_ORDER],
    }


# --- retrieval before planning ---------------------------------------------

def test_plan_retrieves_relevant_memory_and_surfaces_it_in_the_prompt(monkeypatch, tmp_path):
    memory = MemoryStore(db_path=tmp_path / "mem.sqlite3")
    memory.store(
        "Modernise inventory system for logistics client",
        "Past recommendation: strangler-fig migration with SAP integration.",
        agent_id="report",
    )
    executive = AIExecutive(memory=memory)

    captured = {}

    def fake_call_llm_json(system, user, **kwargs):
        captured["user"] = user
        return _mock_plan_response(system, user)

    monkeypatch.setattr("backend.ai_executive.call_llm_json", fake_call_llm_json)

    plan = executive.plan("Modernise inventory system for another logistics client with SAP")

    assert len(plan.retrieved_memory) == 1
    assert "strangler-fig migration" in captured["user"]


def test_plan_with_no_matching_memory_has_empty_retrieved_memory(monkeypatch, tmp_path):
    memory = MemoryStore(db_path=tmp_path / "mem.sqlite3")
    executive = AIExecutive(memory=memory)
    monkeypatch.setattr("backend.ai_executive.call_llm_json", _mock_plan_response)

    plan = executive.plan("A brand new task with nothing stored yet")

    assert plan.retrieved_memory == ()


def test_plan_continues_without_memory_when_memory_store_unavailable(monkeypatch, tmp_path):
    blocking_file = tmp_path / "not_a_directory"
    blocking_file.write_text("blocker")
    with pytest.warns(RuntimeWarning):
        broken_memory = MemoryStore(db_path=blocking_file / "sub" / "mem.sqlite3")

    executive = AIExecutive(memory=broken_memory)
    monkeypatch.setattr("backend.ai_executive.call_llm_json", _mock_plan_response)

    plan = executive.plan("Any task")  # must not raise

    assert plan.retrieved_memory == ()
    assert plan.fallback_used is False


# --- persistence after dispatch, and the full store->retrieve loop ---------

def test_dispatch_persists_final_report_to_memory(monkeypatch, tmp_path):
    memory = MemoryStore(db_path=tmp_path / "mem.sqlite3")
    executive = AIExecutive(memory=memory)
    monkeypatch.setattr("backend.ai_executive.call_llm_json", _mock_plan_response)

    task = "A mid-size logistics client wants to modernise their inventory system."
    result = executive.dispatch(task)

    assert result.workflow_result["status"] == "complete"
    stored = memory.search(task)
    assert len(stored) == 1
    assert stored[0].content == result.workflow_result["final_report"]
    assert stored[0].agent_id == "report"
    assert stored[0].metadata["status"] == "complete"
    assert list(stored[0].metadata["selected_agents"]) == list(DEFAULT_PIPELINE_ORDER)


def test_memory_persisted_from_one_dispatch_is_retrieved_by_a_later_plan(monkeypatch, tmp_path):
    """Simulates reuse across requests (or an app restart, since MemoryStore
    re-opens the same sqlite file): a second AIExecutive instance sharing the
    same MemoryStore should see what an earlier dispatch() persisted."""
    shared_memory = MemoryStore(db_path=tmp_path / "mem.sqlite3")
    monkeypatch.setattr("backend.ai_executive.call_llm_json", _mock_plan_response)

    first_executive = AIExecutive(memory=shared_memory)
    task = "Modernise an on-prem inventory system with SAP integration for a logistics client."
    first_result = first_executive.dispatch(task)

    # A brand new MemoryStore instance pointed at the same file — as if the
    # process had restarted — plus a brand new AIExecutive using it.
    reopened_memory = MemoryStore(db_path=tmp_path / "mem.sqlite3")
    second_executive = AIExecutive(memory=reopened_memory)

    plan = second_executive.plan("Modernise inventory and SAP integration for a similar logistics client.")

    assert len(plan.retrieved_memory) == 1
    assert plan.retrieved_memory[0].content == first_result.workflow_result["final_report"]


def test_dispatch_does_not_store_memory_when_workflow_has_no_final_report(monkeypatch, tmp_path):
    memory = MemoryStore(db_path=tmp_path / "mem.sqlite3")
    executive = AIExecutive(memory=memory)
    monkeypatch.setattr("backend.ai_executive.call_llm_json", _mock_plan_response)
    monkeypatch.setattr(
        "backend.ai_executive.workflow_graph.invoke",
        lambda initial_state: {**initial_state, "status": "failed", "log": []},
    )

    result = executive.dispatch("A task that fails before reporting.")

    assert result.workflow_result.get("final_report") is None
    assert memory.search("A task that fails before reporting.") == []


# --- fail-safe integration --------------------------------------------------

def test_dispatch_still_completes_when_memory_store_is_unavailable(monkeypatch, tmp_path):
    blocking_file = tmp_path / "not_a_directory"
    blocking_file.write_text("blocker")
    with pytest.warns(RuntimeWarning):
        broken_memory = MemoryStore(db_path=blocking_file / "sub" / "mem.sqlite3")

    executive = AIExecutive(memory=broken_memory)
    monkeypatch.setattr("backend.ai_executive.call_llm_json", _mock_plan_response)

    result = executive.dispatch("A task despite a broken memory backend.")

    assert result.workflow_result["status"] == "complete"
    assert result.workflow_result.get("final_report")
    assert result.retrieved_memory == ()


# --- WorkflowState isolation -------------------------------------------------

def test_memory_is_never_merged_into_workflow_state(monkeypatch, tmp_path):
    """Constraint: memory must stay separate from WorkflowState — dispatch()
    must not inject retrieved memory (or anything memory-shaped) into the
    initial_state passed to workflow_graph.invoke()."""
    memory = MemoryStore(db_path=tmp_path / "mem.sqlite3")
    memory.store("Some prior task", "Some prior content", agent_id="report")
    executive = AIExecutive(memory=memory)
    monkeypatch.setattr("backend.ai_executive.call_llm_json", _mock_plan_response)

    from backend.ai_executive import workflow_graph

    captured_state = {}
    real_invoke = workflow_graph.invoke

    def spy_invoke(initial_state):
        captured_state.update(initial_state)
        return real_invoke(initial_state)

    monkeypatch.setattr("backend.ai_executive.workflow_graph.invoke", spy_invoke)

    executive.dispatch("Some prior task variant")

    assert "memory" not in captured_state
    assert "retrieved_memory" not in captured_state
    assert set(captured_state.keys()) == {"request_id", "raw_request", "status", "log"}
