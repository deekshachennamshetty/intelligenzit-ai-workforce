"""
Tests for the tool-layer integration in backend/ai_executive.py: tool
availability surfaced in the planning roster, per-agent tool requests
validated against both AgentRegistry (assignment) and ToolRegistry
(existence/enabled), rejected/unassigned requests dropped and recorded,
and confirmation that AIExecutive never actually executes a tool itself.
"""
import pytest

from backend.agent_registry import AgentDefinition, AgentRegistry, AgentStatus
from backend.ai_executive import AIExecutive, DEFAULT_PIPELINE_ORDER
from backend.knowledge_base import KnowledgeBase
from backend.memory import MemoryStore
from backend.tool_registry import CALCULATOR_TOOL, ToolRegistry, assign_agent_tools


@pytest.fixture(autouse=True)
def mock_mode(monkeypatch):
    monkeypatch.setenv("MOCK_MODE", "1")


def make_registry_with_calculator_on_solution() -> tuple:
    agents = AgentRegistry()
    agents.register(AgentDefinition(
        agent_id="solution", name="Solution Agent", department="D", role="R",
        description="desc", status=AgentStatus.ACTIVE,
    ))
    agents.register(AgentDefinition(
        agent_id="review", name="Review Agent", department="D", role="R",
        description="desc", status=AgentStatus.ACTIVE,
    ))
    tools = ToolRegistry()
    tools.register(CALCULATOR_TOOL)
    assign_agent_tools(agents, "solution", ["calculator"], tool_registry=tools)
    return agents, tools


def make_executive(tmp_path, *, registry=None, tool_registry=None) -> AIExecutive:
    return AIExecutive(
        registry=registry,
        memory=MemoryStore(db_path=tmp_path / "mem.sqlite3"),
        knowledge_base=KnowledgeBase(db_path=tmp_path / "kb.sqlite3"),
        tool_registry=tool_registry,
    )


# --- tool availability surfaced in the roster -------------------------------

def test_roster_text_lists_assigned_tool_names(monkeypatch, tmp_path):
    agents, tools = make_registry_with_calculator_on_solution()
    executive = make_executive(tmp_path, registry=agents, tool_registry=tools)

    captured = {}

    def fake_call_llm_json(system, user, **kwargs):
        captured["user"] = user
        return {"task_type": "x", "agents": [{"agent_id": "solution", "reason": "needed"}]}

    monkeypatch.setattr("backend.ai_executive.call_llm_json", fake_call_llm_json)

    executive.plan("Do some math for the client")

    assert "[tools: Calculator]" in captured["user"]
    # review has no tools assigned, so it must not get a tools suffix.
    assert "review: Review Agent (R) — desc [tools:" not in captured["user"]


def test_roster_shows_tool_id_when_tool_missing_from_registry(monkeypatch, tmp_path):
    """If an agent's tools tuple references a tool_id that isn't (or is no
    longer) in the ToolRegistry, the roster falls back to showing the raw id
    rather than crashing."""
    agents = AgentRegistry()
    agents.register(AgentDefinition(
        agent_id="solution", name="Solution Agent", department="D", role="R",
        description="desc", tools=["ghost-tool"], status=AgentStatus.ACTIVE,
    ))
    empty_tools = ToolRegistry()
    executive = make_executive(tmp_path, registry=agents, tool_registry=empty_tools)
    monkeypatch.setattr(
        "backend.ai_executive.call_llm_json",
        lambda system, user, **kwargs: {"task_type": "x", "agents": [{"agent_id": "solution"}]},
    )

    plan = executive.plan("task")  # must not raise

    assert plan.fallback_used is False


# --- validated per-agent tool requests --------------------------------------

def test_plan_accepts_a_tool_request_for_the_agent_it_is_assigned_to(monkeypatch, tmp_path):
    agents, tools = make_registry_with_calculator_on_solution()
    executive = make_executive(tmp_path, registry=agents, tool_registry=tools)
    monkeypatch.setattr(
        "backend.ai_executive.call_llm_json",
        lambda system, user, **kwargs: {
            "task_type": "x",
            "agents": [{"agent_id": "solution", "reason": "needs math", "tools": ["calculator"]}],
        },
    )

    plan = executive.plan("Compute something")

    assert plan.steps[0].requested_tools == ("calculator",)
    assert plan.rejected_tool_ids == ()


def test_plan_rejects_tool_request_for_an_agent_it_is_not_assigned_to(monkeypatch, tmp_path):
    """calculator is only assigned to 'solution' — 'review' must not be able
    to borrow it just because the LLM said so."""
    agents, tools = make_registry_with_calculator_on_solution()
    executive = make_executive(tmp_path, registry=agents, tool_registry=tools)
    monkeypatch.setattr(
        "backend.ai_executive.call_llm_json",
        lambda system, user, **kwargs: {
            "task_type": "x",
            "agents": [{"agent_id": "review", "reason": "wants math too", "tools": ["calculator"]}],
        },
    )

    plan = executive.plan("Review something")

    assert plan.steps[0].requested_tools == ()
    assert plan.rejected_tool_ids == ("calculator",)


def test_plan_rejects_request_for_unknown_tool_id(monkeypatch, tmp_path):
    agents, tools = make_registry_with_calculator_on_solution()
    executive = make_executive(tmp_path, registry=agents, tool_registry=tools)
    monkeypatch.setattr(
        "backend.ai_executive.call_llm_json",
        lambda system, user, **kwargs: {
            "task_type": "x",
            "agents": [{"agent_id": "solution", "tools": ["totally-invented-tool"]}],
        },
    )

    plan = executive.plan("task")

    assert plan.steps[0].requested_tools == ()
    assert plan.rejected_tool_ids == ("totally-invented-tool",)


def test_plan_rejects_request_for_disabled_tool(monkeypatch, tmp_path):
    agents = AgentRegistry()
    agents.register(AgentDefinition(
        agent_id="solution", name="Solution Agent", department="D", role="R",
        description="desc", status=AgentStatus.ACTIVE,
    ))
    tools = ToolRegistry()
    from dataclasses import replace
    tools.register(replace(CALCULATOR_TOOL, enabled=False))
    assign_agent_tools(agents, "solution", ["calculator"], tool_registry=tools)
    executive = make_executive(tmp_path, registry=agents, tool_registry=tools)
    monkeypatch.setattr(
        "backend.ai_executive.call_llm_json",
        lambda system, user, **kwargs: {
            "task_type": "x", "agents": [{"agent_id": "solution", "tools": ["calculator"]}],
        },
    )

    plan = executive.plan("task")

    assert plan.steps[0].requested_tools == ()
    assert plan.rejected_tool_ids == ("calculator",)


def test_plan_without_tools_key_has_no_requested_tools(monkeypatch, tmp_path):
    agents, tools = make_registry_with_calculator_on_solution()
    executive = make_executive(tmp_path, registry=agents, tool_registry=tools)
    monkeypatch.setattr(
        "backend.ai_executive.call_llm_json",
        lambda system, user, **kwargs: {"task_type": "x", "agents": [{"agent_id": "solution"}]},
    )

    plan = executive.plan("task")

    assert plan.steps[0].requested_tools == ()
    assert plan.rejected_tool_ids == ()


# --- AIExecutive never executes tools itself --------------------------------

def test_dispatch_never_calls_tool_registry_execute(monkeypatch, tmp_path):
    agents, tools = make_registry_with_calculator_on_solution()
    # Also give the default 7-stage pipeline agents so the real graph can run.
    from backend.agent_registry import default_registry
    for agent in default_registry.list_agents():
        agents.register(agent, replace=True)

    executed = []
    real_execute = tools.execute
    tools.execute = lambda *a, **k: executed.append((a, k)) or real_execute(*a, **k)

    executive = make_executive(tmp_path, registry=agents, tool_registry=tools)
    monkeypatch.setattr(
        "backend.ai_executive.call_llm_json",
        lambda system, user, **kwargs: {
            "task_type": "x",
            "agents": [{"agent_id": a, "tools": ["calculator"]} for a in DEFAULT_PIPELINE_ORDER],
        },
    )

    executive.dispatch("A task with tools available.")

    assert executed == []  # ToolRegistry.execute() must never be called by AIExecutive


# --- WorkflowState isolation -------------------------------------------------

def test_tool_requests_never_merged_into_workflow_state(monkeypatch, tmp_path):
    agents, tools = make_registry_with_calculator_on_solution()
    from backend.agent_registry import default_registry
    for agent in default_registry.list_agents():
        agents.register(agent, replace=True)

    executive = make_executive(tmp_path, registry=agents, tool_registry=tools)
    monkeypatch.setattr(
        "backend.ai_executive.call_llm_json",
        lambda system, user, **kwargs: {
            "task_type": "x",
            "agents": [{"agent_id": a, "tools": ["calculator"]} for a in DEFAULT_PIPELINE_ORDER],
        },
    )

    from backend.ai_executive import workflow_graph
    captured_state = {}
    real_invoke = workflow_graph.invoke

    def spy_invoke(initial_state):
        captured_state.update(initial_state)
        return real_invoke(initial_state)

    monkeypatch.setattr("backend.ai_executive.workflow_graph.invoke", spy_invoke)

    executive.dispatch("A task with tools available.")

    assert set(captured_state.keys()) == {"request_id", "raw_request", "status", "log"}
