"""
Tests for backend/tool_registry.py: ToolDefinition/ToolResult validation,
registration, lookup, listing, safe execution (unknown/disabled/invalid
input/bad result/handler-exception all fail safe as a structured
ToolResult), the built-in calculator tool's safety, and assign_agent_tools().
"""
import pytest

from backend.agent_registry import AgentDefinition, AgentNotFoundError, AgentRegistry, AgentStatus
from backend.tool_registry import (
    CALCULATOR_TOOL,
    DuplicateToolError,
    ToolDefinition,
    ToolNotFoundError,
    ToolParameter,
    ToolRegistry,
    ToolResult,
    ToolValidationError,
    assign_agent_tools,
    calculator_tool,
    default_tool_registry,
    validate_tool,
)


def make_tool(**overrides) -> ToolDefinition:
    fields = dict(
        tool_id="echo",
        name="Echo",
        description="Echoes the given text back.",
        handler=lambda text: text,
        parameters=(ToolParameter(name="text", type="str", required=True),),
    )
    fields.update(overrides)
    return ToolDefinition(**fields)


def make_agent(**overrides) -> AgentDefinition:
    fields = dict(
        agent_id="test-agent", name="Test Agent", department="D", role="R",
        description="desc", status=AgentStatus.ACTIVE,
    )
    fields.update(overrides)
    return AgentDefinition(**fields)


# --- validation --------------------------------------------------------

def test_valid_tool_passes_validation():
    validate_tool(make_tool())  # should not raise


@pytest.mark.parametrize("bad_id", ["", "Bad-ID", "has spaces", "_leading", "UPPER"])
def test_invalid_tool_id_rejected(bad_id):
    with pytest.raises(ToolValidationError, match="tool_id"):
        validate_tool(make_tool(tool_id=bad_id))


def test_blank_name_rejected():
    with pytest.raises(ToolValidationError, match="name"):
        validate_tool(make_tool(name="   "))


def test_blank_description_rejected():
    with pytest.raises(ToolValidationError, match="description"):
        validate_tool(make_tool(description="   "))


def test_non_callable_handler_rejected():
    with pytest.raises(ToolValidationError, match="handler"):
        validate_tool(make_tool(handler="not a function"))


def test_invalid_parameter_type_rejected():
    with pytest.raises(ToolValidationError, match="type"):
        validate_tool(make_tool(parameters=(ToolParameter(name="x", type="not-a-type"),)))


def test_non_bool_enabled_rejected():
    with pytest.raises(ToolValidationError, match="enabled"):
        validate_tool(make_tool(enabled="yes"))


# --- registration / lookup / listing ---------------------------------------

def test_register_and_get_roundtrip():
    registry = ToolRegistry()
    tool = make_tool()
    registry.register(tool)
    assert registry.get("echo") is tool


def test_register_invalid_tool_raises_before_storing():
    registry = ToolRegistry()
    with pytest.raises(ToolValidationError):
        registry.register(make_tool(tool_id="Not Valid"))
    assert len(registry) == 0


def test_duplicate_registration_rejected_without_replace():
    registry = ToolRegistry()
    registry.register(make_tool())
    with pytest.raises(DuplicateToolError):
        registry.register(make_tool(name="Different"))


def test_duplicate_registration_allowed_with_replace():
    registry = ToolRegistry()
    registry.register(make_tool(name="Original"))
    registry.register(make_tool(name="Updated"), replace=True)
    assert registry.get("echo").name == "Updated"


def test_get_missing_tool_raises():
    registry = ToolRegistry()
    with pytest.raises(ToolNotFoundError):
        registry.get("does-not-exist")


def test_get_or_none_missing_tool_returns_none():
    registry = ToolRegistry()
    assert registry.get_or_none("does-not-exist") is None


def test_list_tools_sorted_and_filterable_by_enabled():
    registry = ToolRegistry()
    registry.register(make_tool(tool_id="b_tool", enabled=False))
    registry.register(make_tool(tool_id="a_tool", enabled=True))

    assert [t.tool_id for t in registry.list_tools()] == ["a_tool", "b_tool"]
    assert [t.tool_id for t in registry.list_tools(enabled=True)] == ["a_tool"]
    assert [t.tool_id for t in registry.list_tools(enabled=False)] == ["b_tool"]


# --- execute(): the safe entry point ----------------------------------------

def test_execute_unknown_tool_fails_safe():
    registry = ToolRegistry()
    result = registry.execute("nonexistent", {})
    assert isinstance(result, ToolResult)
    assert result.success is False
    assert result.error_code == "unknown_tool"


def test_execute_disabled_tool_fails_safe():
    registry = ToolRegistry()
    registry.register(make_tool(enabled=False))
    result = registry.execute("echo", {"text": "hi"})
    assert result.success is False
    assert result.error_code == "disabled"


def test_execute_missing_required_input_fails_safe():
    registry = ToolRegistry()
    registry.register(make_tool())
    result = registry.execute("echo", {})
    assert result.success is False
    assert result.error_code == "invalid_input"


def test_execute_unexpected_input_fails_safe():
    registry = ToolRegistry()
    registry.register(make_tool())
    result = registry.execute("echo", {"text": "hi", "extra": 1})
    assert result.success is False
    assert result.error_code == "invalid_input"


def test_execute_wrong_input_type_fails_safe():
    registry = ToolRegistry()
    registry.register(make_tool())
    result = registry.execute("echo", {"text": 123})
    assert result.success is False
    assert result.error_code == "invalid_input"


def test_execute_bool_not_accepted_as_int():
    registry = ToolRegistry()
    registry.register(make_tool(
        tool_id="adder", name="Adder", description="Adds one to a number.",
        handler=lambda n: n + 1,
        parameters=(ToolParameter(name="n", type="int", required=True),),
    ))
    result = registry.execute("adder", {"n": True})  # bool is an int subclass; must be rejected
    assert result.success is False
    assert result.error_code == "invalid_input"


def test_execute_optional_input_can_be_omitted():
    registry = ToolRegistry()
    registry.register(make_tool(
        tool_id="greeter", name="Greeter", description="Greets someone.",
        handler=lambda name="world": f"hello {name}",
        parameters=(ToolParameter(name="name", type="str", required=False),),
    ))
    result = registry.execute("greeter", {})
    assert result.success is True
    assert result.output == "hello world"


def test_execute_handler_exception_fails_safe():
    registry = ToolRegistry()
    registry.register(make_tool(
        tool_id="boom", name="Boom", description="Always raises.",
        handler=lambda: (_ for _ in ()).throw(RuntimeError("kaboom")),
        parameters=(),
    ))
    result = registry.execute("boom", {})
    assert result.success is False
    assert result.error_code == "execution_error"
    assert "kaboom" in result.error


def test_execute_unsafe_result_type_fails_safe():
    registry = ToolRegistry()
    registry.register(make_tool(
        tool_id="bad_result", name="Bad Result", description="Returns an unsafe object.",
        handler=lambda: object(),
        parameters=(),
    ))
    result = registry.execute("bad_result", {})
    assert result.success is False
    assert result.error_code == "invalid_result"


def test_execute_success_returns_output():
    registry = ToolRegistry()
    registry.register(make_tool())
    result = registry.execute("echo", {"text": "hello"})
    assert result == ToolResult(tool_id="echo", success=True, output="hello")


# --- calculator: the example tool, and its safety ---------------------------

def test_calculator_evaluates_basic_arithmetic():
    assert calculator_tool("2 + 3 * 4") == 14
    assert calculator_tool("(2 + 3) * 4") == 20
    assert calculator_tool("2 ** 10") == 1024
    assert calculator_tool("-5 + 2") == -3


@pytest.mark.parametrize("expression", [
    "__import__('os').system('echo hi')",
    "open('/etc/passwd')",
    "1; import os",
    "[x for x in range(3)]",
    "a + 1",
])
def test_calculator_rejects_anything_beyond_arithmetic(expression):
    with pytest.raises(ValueError):
        calculator_tool(expression)


def test_calculator_registered_in_default_tool_registry():
    assert default_tool_registry.get("calculator") is CALCULATOR_TOOL


def test_calculator_via_registry_execute():
    result = default_tool_registry.execute("calculator", {"expression": "6 * 7"})
    assert result.success is True
    assert result.output == 42


def test_calculator_invalid_expression_fails_safe_via_registry():
    result = default_tool_registry.execute("calculator", {"expression": "__import__('os')"})
    assert result.success is False
    assert result.error_code == "execution_error"


# --- assign_agent_tools(): explicit, validated assignment -------------------

def test_assign_agent_tools_updates_agent_tool_list():
    agents = AgentRegistry()
    agents.register(make_agent())
    tools = ToolRegistry()
    tools.register(make_tool())

    updated = assign_agent_tools(agents, "test-agent", ["echo"], tool_registry=tools)

    assert updated.tools == ("echo",)
    assert agents.get("test-agent").tools == ("echo",)


def test_assign_agent_tools_replaces_previous_assignment():
    agents = AgentRegistry()
    agents.register(make_agent(tools=["old-tool"]))
    tools = ToolRegistry()
    tools.register(make_tool())

    assign_agent_tools(agents, "test-agent", ["echo"], tool_registry=tools)

    assert agents.get("test-agent").tools == ("echo",)


def test_assign_agent_tools_raises_for_missing_agent():
    agents = AgentRegistry()
    tools = ToolRegistry()
    tools.register(make_tool())
    with pytest.raises(AgentNotFoundError):
        assign_agent_tools(agents, "does-not-exist", ["echo"], tool_registry=tools)


def test_assign_agent_tools_raises_for_unregistered_tool():
    agents = AgentRegistry()
    agents.register(make_agent())
    tools = ToolRegistry()
    with pytest.raises(ToolNotFoundError):
        assign_agent_tools(agents, "test-agent", ["does-not-exist"], tool_registry=tools)


def test_assign_agent_tools_defaults_to_default_tool_registry():
    agents = AgentRegistry()
    agents.register(make_agent())
    updated = assign_agent_tools(agents, "test-agent", ["calculator"])
    assert updated.tools == ("calculator",)
