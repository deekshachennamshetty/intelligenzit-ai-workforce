"""
Tests for backend/agent_registry.py: definition validation, registration,
lookup, listing/filtering, model config resolution, and the pre-populated
default registry that mirrors the 7 live pipeline agents in backend/agents.py.
"""
import pytest

from backend.agent_registry import (
    AgentDefinition,
    AgentNotFoundError,
    AgentRegistry,
    AgentStatus,
    AgentValidationError,
    DuplicateAgentError,
    ModelConfig,
    build_default_registry,
    default_registry,
    validate_agent,
)


def make_agent(**overrides) -> AgentDefinition:
    fields = dict(
        agent_id="test-agent",
        name="Test Agent",
        department="Testing",
        role="Does test things",
        description="An agent used only in tests.",
        capabilities=["testing"],
        tools=[],
        status=AgentStatus.ACTIVE,
    )
    fields.update(overrides)
    return AgentDefinition(**fields)


# --- Validation ---------------------------------------------------------

def test_valid_agent_passes_validation():
    validate_agent(make_agent())  # should not raise


@pytest.mark.parametrize("bad_id", ["", "Bad-ID", "has spaces", "_leading-underscore", "UPPER"])
def test_invalid_agent_id_rejected(bad_id):
    with pytest.raises(AgentValidationError, match="agent_id"):
        validate_agent(make_agent(agent_id=bad_id))


@pytest.mark.parametrize("field_name", ["name", "department", "role", "description"])
def test_blank_required_string_field_rejected(field_name):
    with pytest.raises(AgentValidationError, match=field_name):
        validate_agent(make_agent(**{field_name: "   "}))


def test_non_string_capability_rejected():
    with pytest.raises(AgentValidationError, match="capabilities"):
        validate_agent(make_agent(capabilities=["ok", 123]))


def test_non_string_tool_rejected():
    with pytest.raises(AgentValidationError, match="tools"):
        validate_agent(make_agent(tools=["ok", None]))


def test_invalid_model_provider_rejected():
    with pytest.raises(AgentValidationError, match="provider"):
        validate_agent(make_agent(model=ModelConfig(provider="not-a-real-provider")))


def test_non_positive_max_tokens_rejected():
    with pytest.raises(AgentValidationError, match="max_tokens"):
        validate_agent(make_agent(model=ModelConfig(max_tokens=0)))


def test_wrong_status_type_rejected():
    with pytest.raises(AgentValidationError, match="status"):
        validate_agent(make_agent(status="active"))  # must be AgentStatus, not a raw string


# --- Registration / lookup / removal ------------------------------------

def test_register_and_get_roundtrip():
    registry = AgentRegistry()
    agent = make_agent()
    registry.register(agent)
    assert registry.get("test-agent") is agent


def test_register_invalid_definition_raises_before_storing():
    registry = AgentRegistry()
    with pytest.raises(AgentValidationError):
        registry.register(make_agent(agent_id="Not Valid"))
    assert len(registry) == 0


def test_duplicate_registration_rejected_without_replace():
    registry = AgentRegistry()
    registry.register(make_agent())
    with pytest.raises(DuplicateAgentError):
        registry.register(make_agent(name="Different Name"))


def test_duplicate_registration_allowed_with_replace():
    registry = AgentRegistry()
    registry.register(make_agent(name="Original"))
    registry.register(make_agent(name="Updated"), replace=True)
    assert registry.get("test-agent").name == "Updated"


def test_get_missing_agent_raises():
    registry = AgentRegistry()
    with pytest.raises(AgentNotFoundError):
        registry.get("does-not-exist")


def test_get_or_none_missing_agent_returns_none():
    registry = AgentRegistry()
    assert registry.get_or_none("does-not-exist") is None


def test_unregister_removes_agent():
    registry = AgentRegistry()
    registry.register(make_agent())
    registry.unregister("test-agent")
    assert "test-agent" not in registry
    assert len(registry) == 0


def test_unregister_missing_agent_raises():
    registry = AgentRegistry()
    with pytest.raises(AgentNotFoundError):
        registry.unregister("does-not-exist")


# --- Listing / filtering --------------------------------------------------

def test_list_agents_sorted_by_id():
    registry = AgentRegistry()
    registry.register(make_agent(agent_id="zebra"))
    registry.register(make_agent(agent_id="alpha"))
    ids = [a.agent_id for a in registry.list_agents()]
    assert ids == ["alpha", "zebra"]


def test_list_agents_filters_by_department():
    registry = AgentRegistry()
    registry.register(make_agent(agent_id="a1", department="Dept A"))
    registry.register(make_agent(agent_id="a2", department="Dept B"))
    result = registry.list_agents(department="Dept A")
    assert [a.agent_id for a in result] == ["a1"]


def test_list_agents_filters_by_status():
    registry = AgentRegistry()
    registry.register(make_agent(agent_id="a1", status=AgentStatus.ACTIVE))
    registry.register(make_agent(agent_id="a2", status=AgentStatus.DEPRECATED))
    result = registry.list_agents(status=AgentStatus.DEPRECATED)
    assert [a.agent_id for a in result] == ["a2"]


def test_list_agents_filters_by_capability():
    registry = AgentRegistry()
    registry.register(make_agent(agent_id="a1", capabilities=["research"]))
    registry.register(make_agent(agent_id="a2", capabilities=["reporting"]))
    result = registry.list_agents(capability="reporting")
    assert [a.agent_id for a in result] == ["a2"]


# --- ModelConfig resolution ------------------------------------------------

def test_model_config_resolve_uses_llm_defaults_when_unset(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_MODEL", "llama3.1")
    resolved = ModelConfig().resolve()
    assert resolved.provider == "ollama"
    assert resolved.model == "llama3.1"


def test_model_config_resolve_respects_explicit_overrides(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_MODEL", "llama3.1")
    resolved = ModelConfig(provider="anthropic", model="claude-sonnet-4-6").resolve()
    assert resolved.provider == "anthropic"
    assert resolved.model == "claude-sonnet-4-6"


# --- Default registry (mirrors the live 7-stage pipeline) -----------------

def test_default_registry_contains_all_seven_pipeline_agents():
    expected_ids = {
        "orchestrator", "research", "requirements",
        "analysis", "solution", "review", "report",
    }
    assert {a.agent_id for a in default_registry.list_agents()} == expected_ids


def test_default_registry_agents_are_all_active_and_valid():
    for agent in default_registry.list_agents():
        assert agent.status == AgentStatus.ACTIVE
        validate_agent(agent)  # should not raise


def test_build_default_registry_returns_fresh_independent_instance():
    a = build_default_registry()
    b = build_default_registry()
    assert a is not b
    a.unregister("orchestrator")
    assert "orchestrator" in b
