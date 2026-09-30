"""
Tests for backend/ai_executive.py: plan validation/fallback logic against
AgentRegistry, the LLM-planning call wiring, and full dispatch through the
(unmodified) LangGraph workflow, run in MOCK_MODE so no live provider or
network is required.
"""
import pytest

from backend.agent_registry import AgentDefinition, AgentRegistry, AgentStatus, default_registry
from backend.ai_executive import (
    AIExecutive,
    DEFAULT_PIPELINE_ORDER,
    ExecutiveResult,
    build_plan_from_llm_response,
)


def make_registry():
    registry = AgentRegistry()
    registry.register(AgentDefinition(
        agent_id="alpha", name="Alpha", department="D", role="R",
        description="desc", capabilities=["x"], status=AgentStatus.ACTIVE,
    ))
    registry.register(AgentDefinition(
        agent_id="beta", name="Beta", department="D", role="R",
        description="desc", capabilities=["y"], status=AgentStatus.ACTIVE,
    ))
    registry.register(AgentDefinition(
        agent_id="gamma-inactive", name="Gamma", department="D", role="R",
        description="desc", capabilities=["z"], status=AgentStatus.INACTIVE,
    ))
    return registry


# --- build_plan_from_llm_response: validation & fallback -------------------

def test_valid_plan_selects_registered_active_agents_in_given_order():
    registry = make_registry()
    raw = {"task_type": "custom", "agents": [
        {"agent_id": "beta", "reason": "needed first"},
        {"agent_id": "alpha", "reason": "needed second"},
    ]}
    plan = build_plan_from_llm_response(raw, registry)

    assert plan.fallback_used is False
    assert plan.task_type == "custom"
    assert plan.agent_ids == ("beta", "alpha")
    assert plan.rejected_agent_ids == ()


def test_unknown_agent_id_is_rejected_and_recorded():
    registry = make_registry()
    raw = {"task_type": "x", "agents": [
        {"agent_id": "alpha", "reason": "ok"},
        {"agent_id": "not-a-real-agent", "reason": "invented"},
    ]}
    plan = build_plan_from_llm_response(raw, registry)

    assert plan.fallback_used is False
    assert plan.agent_ids == ("alpha",)
    assert plan.rejected_agent_ids == ("not-a-real-agent",)


def test_inactive_agent_is_rejected_and_recorded():
    registry = make_registry()
    raw = {"task_type": "x", "agents": [{"agent_id": "gamma-inactive", "reason": "no"}]}
    plan = build_plan_from_llm_response(raw, registry)

    assert plan.fallback_used is True  # nothing valid survived
    assert "gamma-inactive" in plan.rejected_agent_ids
    assert "gamma-inactive" not in plan.agent_ids


def test_plan_accepts_plain_string_agent_ids():
    registry = make_registry()
    raw = {"task_type": "x", "agents": ["alpha", "beta"]}
    plan = build_plan_from_llm_response(raw, registry)
    assert plan.agent_ids == ("alpha", "beta")


def test_fallback_on_parse_error():
    registry = make_registry()
    plan = build_plan_from_llm_response({"_parse_error": True, "raw": "not json"}, registry)
    assert plan.fallback_used is True


def test_fallback_when_agents_field_missing():
    registry = make_registry()
    plan = build_plan_from_llm_response({"task_type": "x"}, registry)
    assert plan.fallback_used is True


def test_fallback_when_agents_field_not_a_list():
    registry = make_registry()
    plan = build_plan_from_llm_response({"task_type": "x", "agents": "alpha"}, registry)
    assert plan.fallback_used is True


def test_fallback_when_all_selected_agents_are_invalid():
    registry = make_registry()
    raw = {"task_type": "x", "agents": ["invented-1", "invented-2"]}
    plan = build_plan_from_llm_response(raw, registry)
    assert plan.fallback_used is True
    assert set(plan.rejected_agent_ids) == {"invented-1", "invented-2"}


def test_fallback_against_default_registry_matches_canonical_pipeline_order():
    plan = build_plan_from_llm_response({"_parse_error": True}, default_registry)
    assert plan.fallback_used is True
    assert plan.agent_ids == DEFAULT_PIPELINE_ORDER


def test_missing_task_type_defaults_to_general():
    registry = make_registry()
    plan = build_plan_from_llm_response({"agents": [{"agent_id": "alpha"}]}, registry)
    assert plan.task_type == "general"


def test_task_type_echoing_the_full_objective_falls_back_to_general():
    """Reproduces a real failure observed running against live Ollama
    (llama3.1): the model ignored the "short label" instruction and echoed
    the entire objective back as task_type. That raw text must never reach
    the audit trail or the UI event feed as if it were a classification —
    it should fall back to "general" while the rest of the plan (a valid
    agent selection) is still used normally."""
    registry = make_registry()
    echoed_objective = (
        "Zylophase Manufacturing's quality-inspection modernisation is moving to phase two. "
        "Given the prior recommendation, outline the budget and staffing needed for the pilot "
        "cohort rollout on the first production line."
    )
    raw = {"task_type": echoed_objective, "agents": [{"agent_id": "alpha", "reason": "fits"}]}
    plan = build_plan_from_llm_response(raw, registry)

    assert plan.task_type == "general"
    assert plan.fallback_used is False  # the agent selection itself was valid
    assert plan.agent_ids == ("alpha",)


def test_task_type_over_the_length_cap_falls_back_to_general():
    registry = make_registry()
    raw = {"task_type": "a" * 41, "agents": [{"agent_id": "alpha"}]}
    plan = build_plan_from_llm_response(raw, registry)
    assert plan.task_type == "general"


def test_task_type_with_too_many_words_falls_back_to_general():
    registry = make_registry()
    raw = {"task_type": "one two three four five", "agents": [{"agent_id": "alpha"}]}
    plan = build_plan_from_llm_response(raw, registry)
    assert plan.task_type == "general"


def test_task_type_that_is_not_a_string_falls_back_to_general():
    registry = make_registry()
    raw = {"task_type": ["not", "a", "string"], "agents": [{"agent_id": "alpha"}]}
    plan = build_plan_from_llm_response(raw, registry)
    assert plan.task_type == "general"


@pytest.mark.parametrize("label", [
    "BusinessAnalysis", "Implementation", "technology_recommendation",
    "Technology Recommendation", "general", "custom", "x",
])
def test_valid_short_labels_are_preserved(label):
    registry = make_registry()
    raw = {"task_type": label, "agents": [{"agent_id": "alpha"}]}
    plan = build_plan_from_llm_response(raw, registry)
    assert plan.task_type == label


# --- AIExecutive.plan(): LLM call wiring -----------------------------------

def test_plan_calls_call_llm_json_and_builds_validated_plan(monkeypatch):
    registry = make_registry()
    executive = AIExecutive(registry=registry)
    captured = {}

    def fake_call_llm_json(system, user, **kwargs):
        captured["system"] = system
        captured["user"] = user
        return {"task_type": "custom", "agents": [{"agent_id": "alpha", "reason": "fits"}]}

    monkeypatch.setattr("backend.ai_executive.call_llm_json", fake_call_llm_json)

    plan = executive.plan("Do something for a client")

    assert plan.agent_ids == ("alpha",)
    assert "Do something for a client" in captured["user"]
    assert "alpha" in captured["user"]  # roster was included
    assert "beta" in captured["user"]
    assert "gamma-inactive" not in captured["user"]  # inactive agents excluded from roster


def test_plan_falls_back_when_llm_invents_unregistered_agent(monkeypatch):
    registry = make_registry()
    executive = AIExecutive(registry=registry)
    monkeypatch.setattr(
        "backend.ai_executive.call_llm_json",
        lambda system, user, **kwargs: {"task_type": "x", "agents": [{"agent_id": "totally-made-up"}]},
    )

    plan = executive.plan("task")

    assert plan.fallback_used is True
    assert "totally-made-up" in plan.rejected_agent_ids


# --- AIExecutive.dispatch(): full run through the real, unmodified workflow -

@pytest.fixture(autouse=True)
def mock_mode(monkeypatch):
    # Keeps the underlying LangGraph node calls (agents.py -> llm.py)
    # deterministic and network-free for these dispatch tests.
    monkeypatch.setenv("MOCK_MODE", "1")


def test_dispatch_runs_real_workflow_and_returns_structured_result(monkeypatch, tmp_path):
    from backend.knowledge_base import KnowledgeBase
    from backend.memory import MemoryStore

    # default_registry (the real 7-agent pipeline), isolated memory/knowledge
    # stores so this test never touches the real default *.sqlite3 on disk.
    executive = AIExecutive(
        memory=MemoryStore(db_path=tmp_path / "mem.sqlite3"),
        knowledge_base=KnowledgeBase(db_path=tmp_path / "kb.sqlite3"),
    )
    monkeypatch.setattr(
        "backend.ai_executive.call_llm_json",
        lambda system, user, **kwargs: {
            "task_type": "technology_recommendation",
            "agents": [{"agent_id": a, "reason": "part of pipeline"} for a in DEFAULT_PIPELINE_ORDER],
        },
    )

    result = executive.dispatch("A client wants to modernise their inventory system.")

    assert isinstance(result, ExecutiveResult)
    assert result.task == "A client wants to modernise their inventory system."
    assert result.selected_agents == DEFAULT_PIPELINE_ORDER
    assert result.plan.fallback_used is False
    assert result.workflow_result["status"] == "complete"
    assert result.workflow_result.get("final_report")


def test_dispatch_uses_fallback_plan_but_still_runs_full_workflow(monkeypatch, tmp_path):
    """Even when the LLM's plan is unusable, the underlying 7-stage workflow
    must still run unchanged and to completion — the plan is reported, not
    used to alter graph execution."""
    from backend.knowledge_base import KnowledgeBase
    from backend.memory import MemoryStore

    executive = AIExecutive(
        memory=MemoryStore(db_path=tmp_path / "mem.sqlite3"),
        knowledge_base=KnowledgeBase(db_path=tmp_path / "kb.sqlite3"),
    )
    monkeypatch.setattr(
        "backend.ai_executive.call_llm_json",
        lambda system, user, **kwargs: {"_parse_error": True, "raw": "garbage"},
    )

    result = executive.dispatch("Another client task.")

    assert result.plan.fallback_used is True
    assert result.plan.agent_ids == DEFAULT_PIPELINE_ORDER
    assert result.workflow_result["status"] == "complete"
    assert result.workflow_result.get("final_report")


def test_run_task_module_helper_uses_default_executive(monkeypatch, tmp_path):
    from backend import ai_executive
    from backend.knowledge_base import KnowledgeBase
    from backend.memory import MemoryStore

    # Swap the singleton's memory/knowledge_base for isolated stores so this
    # test never touches the real default *.sqlite3 files on disk.
    monkeypatch.setattr(ai_executive.default_executive, "memory", MemoryStore(db_path=tmp_path / "mem.sqlite3"))
    monkeypatch.setattr(
        ai_executive.default_executive, "knowledge_base", KnowledgeBase(db_path=tmp_path / "kb.sqlite3")
    )
    monkeypatch.setattr("backend.ai_executive.call_llm_json", lambda system, user, **kwargs: {"_parse_error": True})

    result = ai_executive.run_task("A quick task")

    assert isinstance(result, ExecutiveResult)
    assert result.workflow_result["status"] == "complete"
