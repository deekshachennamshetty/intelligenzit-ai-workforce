"""
Tool Layer for the AI Workforce: a safe, explicit registry of Python
callables that registered agents may be assigned and request by name.

Safety properties (by construction, not by convention):
- Tools are registered ahead of time, in code, by a developer — never
  invented or imported dynamically from an LLM response. An LLM's output
  can only ever *request* a tool by name (tool_id) plus plain-data inputs.
- ToolRegistry.execute() is the single choke point: it validates the
  tool_id, the tool's `enabled` flag, the input values against the tool's
  declared parameter schema, and the handler's result type — all before
  (and after) running anything. It never uses eval()/exec(), never shells
  out, and never fetches an unrestricted URL; nothing here does either.
- Unknown tools, disabled tools, invalid inputs, and handler exceptions
  never raise out of execute() — they come back as a structured
  ToolResult(success=False, error=..., error_code=...).
- This module is intentionally *not* wired into LangGraph's WorkflowState
  or the 7-stage pipeline, and nothing here calls execute() automatically.
  It's the safe abstraction a future orchestration layer (e.g. n8n, or an
  agent-execution loop) plugs into — this task only builds and validates
  the registry, not an automatic tool-calling loop.
- Every execute() call is recorded to backend/audit.py's AuditStore (which
  redacts+bounds metadata before persisting) — only the tool_id, an
  optional agent_id/request_id, the outcome, and the *names* (never
  values) of the inputs supplied. Audit logging is wrapped so a broken
  audit backend can never block or crash a tool call.

Any future tool added to this registry must keep following these rules:
no shell commands, no arbitrary Python execution, no unrestricted URL
fetching. That's a rule for whoever writes the next `handler`, not
something the registry can enforce for an arbitrary callable.
"""
import ast
import operator
import re
import warnings
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Optional

from .agent_registry import AgentRegistry
from .audit import AuditStore, default_audit_store

_TOOL_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_VALID_PARAM_TYPES = {"str", "int", "float", "bool"}
_SAFE_RESULT_TYPES = (str, int, float, bool, list, dict, tuple, type(None))


class ToolError(Exception):
    """Base class for tool registry errors raised during registration/lookup."""


class ToolValidationError(ToolError):
    """Raised when a ToolDefinition itself is invalid at registration time."""


class ToolNotFoundError(ToolError):
    """Raised by get() (and assign_agent_tools()) when a tool_id isn't registered."""


class DuplicateToolError(ToolError):
    """Raised when registering a tool_id that already exists (without replace=True)."""


@dataclass(frozen=True)
class ToolParameter:
    name: str
    type: str  # one of _VALID_PARAM_TYPES
    required: bool = True
    description: str = ""


@dataclass(frozen=True)
class ToolDefinition:
    tool_id: str
    name: str
    description: str
    handler: Callable[..., Any]
    parameters: tuple = field(default_factory=tuple)  # tuple[ToolParameter, ...]
    enabled: bool = True

    def __post_init__(self):
        object.__setattr__(self, "parameters", tuple(self.parameters))


@dataclass(frozen=True)
class ToolResult:
    tool_id: str
    success: bool
    output: Any = None
    error: Optional[str] = None
    error_code: Optional[str] = None  # "unknown_tool" | "disabled" | "invalid_input" | "invalid_result" | "execution_error"


def validate_tool(definition: ToolDefinition) -> None:
    """Raise ToolValidationError if `definition` violates the registry's schema."""
    errors = []

    if not isinstance(definition.tool_id, str) or not _TOOL_ID_PATTERN.match(definition.tool_id):
        errors.append(
            "tool_id must be a non-empty lowercase slug (letters, digits, '_' or '-', "
            "starting with a letter or digit)"
        )
    if not isinstance(definition.name, str) or not definition.name.strip():
        errors.append("name must be a non-empty string")
    if not isinstance(definition.description, str) or not definition.description.strip():
        errors.append("description must be a non-empty string")
    if not callable(definition.handler):
        errors.append("handler must be a callable")

    for param in definition.parameters:
        if not isinstance(param, ToolParameter):
            errors.append("parameters must be ToolParameter instances")
            continue
        if not isinstance(param.name, str) or not param.name.strip():
            errors.append("each parameter must have a non-empty string name")
        if param.type not in _VALID_PARAM_TYPES:
            errors.append(
                f"parameter '{param.name}' has invalid type '{param.type}' "
                f"(must be one of {sorted(_VALID_PARAM_TYPES)})"
            )

    if not isinstance(definition.enabled, bool):
        errors.append("enabled must be a bool")

    if errors:
        raise ToolValidationError(
            f"Invalid tool definition '{getattr(definition, 'tool_id', '?')}': " + "; ".join(errors)
        )


class ToolRegistry:
    """In-memory registry of tool definitions. Validates on every
    registration; execute() is the single, fail-safe entry point for
    actually running a tool's handler."""

    def __init__(self, audit_store: Optional[AuditStore] = None):
        self._tools: dict = {}
        self.audit_store = audit_store if audit_store is not None else default_audit_store

    def register(self, definition: ToolDefinition, *, replace: bool = False) -> ToolDefinition:
        validate_tool(definition)
        if definition.tool_id in self._tools and not replace:
            raise DuplicateToolError(
                f"Tool '{definition.tool_id}' is already registered (pass replace=True to overwrite)."
            )
        self._tools[definition.tool_id] = definition
        return definition

    def get(self, tool_id: str) -> ToolDefinition:
        try:
            return self._tools[tool_id]
        except KeyError:
            raise ToolNotFoundError(f"No tool registered with id '{tool_id}'") from None

    def get_or_none(self, tool_id: str) -> Optional[ToolDefinition]:
        return self._tools.get(tool_id)

    def list_tools(self, *, enabled: Optional[bool] = None) -> list:
        results = self._tools.values()
        if enabled is not None:
            results = (t for t in results if t.enabled == enabled)
        return sorted(results, key=lambda t: t.tool_id)

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, tool_id: str) -> bool:
        return tool_id in self._tools

    def _validate_inputs(self, definition: ToolDefinition, inputs) -> Optional[str]:
        if not isinstance(inputs, dict):
            return "inputs must be a dict"

        allowed_names = {p.name for p in definition.parameters}
        unexpected = set(inputs) - allowed_names
        if unexpected:
            return f"unexpected input(s): {sorted(unexpected)}"

        type_map = {"str": str, "int": int, "float": (int, float), "bool": bool}
        for param in definition.parameters:
            if param.name not in inputs:
                if param.required:
                    return f"missing required input '{param.name}'"
                continue
            value = inputs[param.name]
            # bool is a subclass of int in Python — don't let a bool satisfy int/float.
            if param.type in ("int", "float") and isinstance(value, bool):
                return f"input '{param.name}' must be of type '{param.type}'"
            if not isinstance(value, type_map[param.type]):
                return f"input '{param.name}' must be of type '{param.type}'"
        return None

    def execute(
        self,
        tool_id: str,
        inputs: Optional[dict] = None,
        *,
        agent_id: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> ToolResult:
        """The single safe entry point for running a tool. Never raises: an
        unknown tool_id, a disabled tool, invalid inputs, an unsafe result
        type, or a handler exception all come back as a structured
        ToolResult(success=False, error=..., error_code=...).

        agent_id/request_id are optional audit context only — they don't
        affect execution and don't need to be supplied."""
        inputs = inputs if inputs is not None else {}

        definition = self._tools.get(tool_id)
        if definition is None:
            result = ToolResult(tool_id=tool_id, success=False, error=f"Unknown tool '{tool_id}'", error_code="unknown_tool")
        elif not definition.enabled:
            result = ToolResult(tool_id=tool_id, success=False, error=f"Tool '{tool_id}' is disabled", error_code="disabled")
        else:
            input_error = self._validate_inputs(definition, inputs)
            if input_error:
                result = ToolResult(tool_id=tool_id, success=False, error=input_error, error_code="invalid_input")
            else:
                try:
                    output = definition.handler(**inputs)
                except Exception as exc:
                    result = ToolResult(tool_id=tool_id, success=False, error=str(exc), error_code="execution_error")
                else:
                    if not isinstance(output, _SAFE_RESULT_TYPES):
                        result = ToolResult(
                            tool_id=tool_id, success=False,
                            error=f"Tool '{tool_id}' returned an unsupported result type: {type(output).__name__}",
                            error_code="invalid_result",
                        )
                    else:
                        result = ToolResult(tool_id=tool_id, success=True, output=output)

        self._audit(tool_id, inputs, agent_id, request_id, result)
        return result

    def _audit(self, tool_id: str, inputs: dict, agent_id, request_id, result: ToolResult) -> None:
        try:
            self.audit_store.append(
                actor="tool_registry",
                action="tool_execute",
                status="success" if result.success else (result.error_code or "failure"),
                request_id=request_id,
                agent_id=agent_id,
                tool_id=tool_id,
                metadata={"input_names": sorted(inputs.keys()) if isinstance(inputs, dict) else []},
            )
        except Exception as exc:  # AuditStore itself fails safe; this is a second net.
            warnings.warn(f"ToolRegistry: audit logging failed, continuing without it ({exc})", RuntimeWarning)


def assign_agent_tools(
    agent_registry: AgentRegistry,
    agent_id: str,
    tool_ids,
    *,
    tool_registry: Optional[ToolRegistry] = None,
):
    """Explicitly assign a set of tools to an agent, replacing its current
    tool list. Both the agent and every tool_id must already be registered —
    raises AgentNotFoundError / ToolNotFoundError rather than silently
    inventing either one. Returns the updated AgentDefinition."""
    tool_registry = tool_registry if tool_registry is not None else default_tool_registry

    agent = agent_registry.get(agent_id)  # raises AgentNotFoundError if missing
    tool_ids = tuple(tool_ids)
    for tool_id in tool_ids:
        tool_registry.get(tool_id)  # raises ToolNotFoundError if missing

    updated = replace(agent, tools=tool_ids)
    agent_registry.register(updated, replace=True)
    return updated


# ---------------------------------------------------------------------------
# Example tool: a calculator. Safe by construction — it parses the
# expression into an AST and walks only a small allow-list of numeric
# literal/operator nodes; it never calls eval()/exec() and so cannot run
# arbitrary Python regardless of what expression string it's given.
# ---------------------------------------------------------------------------

_ALLOWED_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_ALLOWED_UNARY_OPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _safe_eval_node(node):
    if isinstance(node, ast.Expression):
        return _safe_eval_node(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return node.value
        raise ValueError("only numeric literals are allowed")
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BIN_OPS:
        return _ALLOWED_BIN_OPS[type(node.op)](_safe_eval_node(node.left), _safe_eval_node(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARY_OPS:
        return _ALLOWED_UNARY_OPS[type(node.op)](_safe_eval_node(node.operand))
    raise ValueError(f"unsupported expression: {type(node).__name__}")


def calculator_tool(expression: str):
    """Evaluate a basic arithmetic expression (+ - * / // % **, parentheses,
    numeric literals only). No eval()/exec() — parses to an AST first."""
    try:
        parsed = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"invalid expression: {exc}") from exc
    return _safe_eval_node(parsed)


CALCULATOR_TOOL = ToolDefinition(
    tool_id="calculator",
    name="Calculator",
    description="Evaluate a basic arithmetic expression (+ - * / // % **, parentheses, numeric literals only).",
    handler=calculator_tool,
    parameters=(
        ToolParameter(name="expression", type="str", required=True,
                      description="The arithmetic expression to evaluate, e.g. '(2 + 3) * 4'."),
    ),
)


default_tool_registry = ToolRegistry()
default_tool_registry.register(CALCULATOR_TOOL)
