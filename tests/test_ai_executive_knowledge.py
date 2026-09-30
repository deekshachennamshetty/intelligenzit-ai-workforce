"""
Tests for the knowledge-base (RAG) integration in backend/ai_executive.py:
retrieval before planning, source traceability on the plan/result, clear
separation from execution memory, and fail-safe behavior when the
knowledge base is unavailable.
"""
import pytest

from backend.ai_executive import AIExecutive, DEFAULT_PIPELINE_ORDER
from backend.knowledge_base import KnowledgeBase
from backend.memory import MemoryStore


@pytest.fixture(autouse=True)
def mock_mode(monkeypatch):
    monkeypatch.setenv("MOCK_MODE", "1")


def _mock_plan_response(system, user, **kwargs):
    return {
        "task_type": "technology_recommendation",
        "agents": [{"agent_id": a, "reason": "part of pipeline"} for a in DEFAULT_PIPELINE_ORDER],
    }


def make_executive(tmp_path) -> AIExecutive:
    return AIExecutive(
        memory=MemoryStore(db_path=tmp_path / "mem.sqlite3"),
        knowledge_base=KnowledgeBase(db_path=tmp_path / "kb.sqlite3"),
    )


# --- retrieval before planning, and prompt surfacing -----------------------

def test_plan_retrieves_relevant_knowledge_and_surfaces_it_in_the_prompt(monkeypatch, tmp_path):
    executive = make_executive(tmp_path)
    doc_path = tmp_path / "sap_playbook.txt"
    doc_path.write_text("Company playbook: use a strangler-fig migration for SAP integration projects.")
    executive.knowledge_base.ingest(doc_path)

    captured = {}

    def fake_call_llm_json(system, user, **kwargs):
        captured["user"] = user
        return _mock_plan_response(system, user)

    monkeypatch.setattr("backend.ai_executive.call_llm_json", fake_call_llm_json)

    plan = executive.plan("We need a SAP integration approach for a logistics client.")

    assert len(plan.retrieved_knowledge) == 1
    assert "strangler-fig migration" in captured["user"]
    assert "Relevant company knowledge" in captured["user"]


def test_plan_with_no_matching_knowledge_has_empty_retrieved_knowledge(monkeypatch, tmp_path):
    executive = make_executive(tmp_path)
    monkeypatch.setattr("backend.ai_executive.call_llm_json", _mock_plan_response)

    plan = executive.plan("A brand new task with nothing ingested yet")

    assert plan.retrieved_knowledge == ()


def test_plan_continues_without_knowledge_when_kb_unavailable(monkeypatch, tmp_path):
    blocking_file = tmp_path / "not_a_directory"
    blocking_file.write_text("blocker")
    with pytest.warns(RuntimeWarning):
        broken_kb = KnowledgeBase(db_path=blocking_file / "sub" / "kb.sqlite3")

    executive = AIExecutive(memory=MemoryStore(db_path=tmp_path / "mem.sqlite3"), knowledge_base=broken_kb)
    monkeypatch.setattr("backend.ai_executive.call_llm_json", _mock_plan_response)

    plan = executive.plan("Any task")  # must not raise

    assert plan.retrieved_knowledge == ()
    assert plan.fallback_used is False


# --- traceability: sources on plan/result -----------------------------------

def test_retrieved_knowledge_carries_traceable_source_identifiers(monkeypatch, tmp_path):
    executive = make_executive(tmp_path)
    doc_path = tmp_path / "hr_policy.txt"
    doc_path.write_text("HR remote work policy: employees may work from home two days a week.")
    document = executive.knowledge_base.ingest(doc_path)
    monkeypatch.setattr("backend.ai_executive.call_llm_json", _mock_plan_response)

    result = executive.dispatch("What is our remote work policy?")

    assert len(result.retrieved_knowledge) == 1
    source = result.retrieved_knowledge[0]
    assert source.document_id == document.id
    assert source.filename == "hr_policy.txt"
    assert source.chunk_index == 0
    assert "remote work policy" in source.text
    assert source.score > 0


# --- separation from execution memory ---------------------------------------

def test_knowledge_base_and_memory_are_reported_separately(monkeypatch, tmp_path):
    executive = make_executive(tmp_path)
    doc_path = tmp_path / "doc.txt"
    doc_path.write_text("Overlapping keyword content about inventory modernisation.")
    executive.knowledge_base.ingest(doc_path)
    executive.memory.store(
        "inventory modernisation task",
        "Overlapping keyword content about inventory modernisation.",
        agent_id="report",
    )
    monkeypatch.setattr("backend.ai_executive.call_llm_json", _mock_plan_response)

    plan = executive.plan("inventory modernisation")

    assert len(plan.retrieved_knowledge) == 1
    assert len(plan.retrieved_memory) == 1
    # Different types entirely — never merged into one collection.
    assert type(plan.retrieved_knowledge[0]).__name__ == "RetrievedChunk"
    assert type(plan.retrieved_memory[0]).__name__ == "MemoryRecord"


def test_dispatch_never_writes_to_the_knowledge_base(monkeypatch, tmp_path):
    """AIExecutive only reads from the knowledge base — ingestion is a
    separate, explicit action, never an automatic side effect of dispatch()."""
    executive = make_executive(tmp_path)
    monkeypatch.setattr("backend.ai_executive.call_llm_json", _mock_plan_response)

    executive.dispatch("A task that produces a final report.")

    assert executive.knowledge_base.search("task") == []
    assert executive.knowledge_base.search("final report") == []


# --- WorkflowState isolation -------------------------------------------------

def test_knowledge_is_never_merged_into_workflow_state(monkeypatch, tmp_path):
    executive = make_executive(tmp_path)
    doc_path = tmp_path / "doc.txt"
    doc_path.write_text("Some ingested knowledge content.")
    executive.knowledge_base.ingest(doc_path)
    monkeypatch.setattr("backend.ai_executive.call_llm_json", _mock_plan_response)

    from backend.ai_executive import workflow_graph

    captured_state = {}
    real_invoke = workflow_graph.invoke

    def spy_invoke(initial_state):
        captured_state.update(initial_state)
        return real_invoke(initial_state)

    monkeypatch.setattr("backend.ai_executive.workflow_graph.invoke", spy_invoke)

    executive.dispatch("Some ingested knowledge task")

    assert set(captured_state.keys()) == {"request_id", "raw_request", "status", "log"}


# --- fail-safe integration ---------------------------------------------------

def test_dispatch_still_completes_when_knowledge_base_is_unavailable(monkeypatch, tmp_path):
    blocking_file = tmp_path / "not_a_directory"
    blocking_file.write_text("blocker")
    with pytest.warns(RuntimeWarning):
        broken_kb = KnowledgeBase(db_path=blocking_file / "sub" / "kb.sqlite3")

    executive = AIExecutive(memory=MemoryStore(db_path=tmp_path / "mem.sqlite3"), knowledge_base=broken_kb)
    monkeypatch.setattr("backend.ai_executive.call_llm_json", _mock_plan_response)

    result = executive.dispatch("A task despite a broken knowledge base.")

    assert result.workflow_result["status"] == "complete"
    assert result.workflow_result.get("final_report")
    assert result.retrieved_knowledge == ()
