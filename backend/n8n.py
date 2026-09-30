"""
Safe n8n integration layer, extending the existing Tool Registry:

    AI Executive -> ToolRegistry -> n8n Tool -> approved n8n workflow

Safety properties (by construction, not by convention):
- n8n workflows/webhooks are registered ahead of time, in code, by a
  developer (N8NRegistry.register()) — an explicit allowlist. An LLM never
  supplies a URL: it can only ever request a *tool* by name through
  ToolRegistry (see make_n8n_tool()), and that tool's handler is hardcoded
  at registration time to trigger exactly one pre-approved workflow_id.
  There is no code path anywhere here that takes a URL from an LLM
  response or from a tool's caller.
- N8NRegistry.execute() is the single choke point: it validates the
  workflow_id, the `enabled` flag, the HTTP method against the workflow's
  own allowed_methods, and the payload's JSON-compatibility — all before
  any network call — and validates the response shape after. It never
  raises: unknown workflows, disabled workflows, invalid input, timeouts,
  network failures, and bad responses all come back as a structured
  N8NResult(success=False, error=..., error_code=...).
- Only stdlib urllib is used for HTTP; no shell commands, no dynamic code
  execution, no client library that could reach an unapproved host.
- Secrets are never exposed: N8NWorkflowDefinition stores only the *name*
  of an environment variable holding an optional bearer token
  (auth_header_env_var), never the token value itself. The token is read
  fresh from os.environ at request time, used only in that request's
  headers, and never appears in N8NResult, an error message, or anywhere
  else this module writes output. webhook_url is likewise never echoed
  into an error message (validation errors describe the problem, not the
  value); N8NWorkflowDefinition.redacted_url exposes a safe scheme+host
  form for anyone who needs to display or log which workflow ran.
- Nothing here changes LangGraph's WorkflowState or AIExecutive: an n8n
  workflow is only ever reachable by explicitly registering a tool built
  with make_n8n_tool() into a ToolRegistry, exactly like any other tool —
  AIExecutive still never calls ToolRegistry.execute() itself (see
  backend/ai_executive.py, backend/tool_registry.py) and never makes an
  HTTP request directly.
- Every execute() call is recorded to backend/audit.py's AuditStore, under
  the workflow_id (as tool_id) — only status/status_code, never the
  webhook_url, headers, payload, or response body. Audit logging is
  wrapped so a broken audit backend can never block or crash a webhook call.
"""
import json
import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
import warnings
from dataclasses import dataclass, field
from typing import Any, Optional

from .audit import AuditStore, default_audit_store
from .tool_registry import ToolDefinition

_WORKFLOW_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_VALID_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
_JSON_SCALAR_TYPES = (str, int, float, bool, type(None))


class N8NError(Exception):
    """Base class for n8n registry errors raised during registration/config."""


class N8NValidationError(N8NError):
    """Raised when an N8NWorkflowDefinition itself is invalid at registration time."""


class N8NConfigError(N8NError):
    """Raised by N8NWorkflowDefinition.from_env() when required configuration is missing."""


class N8NNotFoundError(N8NError):
    """Raised by get() when a workflow_id isn't registered."""


class DuplicateWorkflowError(N8NError):
    """Raised when registering a workflow_id that already exists (without replace=True)."""


def _is_json_compatible(value: Any) -> bool:
    """True if `value` round-trips through JSON using only plain data —
    the only kind of payload/response this layer will ever send or accept."""
    if isinstance(value, _JSON_SCALAR_TYPES):
        return True
    if isinstance(value, dict):
        return all(isinstance(k, str) and _is_json_compatible(v) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return all(_is_json_compatible(v) for v in value)
    return False


@dataclass(frozen=True)
class N8NWorkflowDefinition:
    workflow_id: str
    name: str
    webhook_url: str
    allowed_methods: tuple = field(default_factory=lambda: ("POST",))
    enabled: bool = True
    description: str = ""
    timeout_seconds: float = 10.0
    auth_header_env_var: Optional[str] = None  # name of an env var holding a bearer token — never the token itself

    def __post_init__(self):
        object.__setattr__(self, "allowed_methods", tuple(m.upper() if isinstance(m, str) else m for m in self.allowed_methods))

    @property
    def redacted_url(self) -> str:
        """Scheme + host only — safe to display or log; never the path/query,
        which is where n8n webhook URLs typically carry their secret token."""
        parsed = urllib.parse.urlparse(self.webhook_url)
        return f"{parsed.scheme}://{parsed.netloc}/***"

    @classmethod
    def from_env(
        cls,
        workflow_id: str,
        name: str,
        *,
        url_env_var: str,
        description: str = "",
        allowed_methods: tuple = ("POST",),
        timeout_seconds: float = 10.0,
        auth_header_env_var: Optional[str] = None,
    ) -> "N8NWorkflowDefinition":
        """Build a definition whose webhook_url is read from an environment
        variable (keeping the deployment-specific, often secret-bearing URL
        out of source code). Raises N8NConfigError — a setup-time error —
        if the env var is missing or blank."""
        webhook_url = os.environ.get(url_env_var)
        if not webhook_url:
            raise N8NConfigError(
                f"Environment variable '{url_env_var}' is not set; cannot configure n8n workflow '{workflow_id}'."
            )
        return cls(
            workflow_id=workflow_id,
            name=name,
            webhook_url=webhook_url,
            allowed_methods=allowed_methods,
            description=description,
            timeout_seconds=timeout_seconds,
            auth_header_env_var=auth_header_env_var,
        )


@dataclass(frozen=True)
class N8NResult:
    """Never carries webhook_url, headers, or any secret — only what's safe
    to log, return to a caller, or hand back through a Tool's ToolResult."""
    workflow_id: str
    success: bool
    status_code: Optional[int] = None
    output: Any = None  # parsed JSON response body, if any (JSON-compatible)
    error: Optional[str] = None
    error_code: Optional[str] = None
    # "unknown_workflow" | "disabled" | "invalid_method" | "invalid_payload"
    # | "timeout" | "network_error" | "http_error" | "invalid_response"


def validate_workflow(definition: N8NWorkflowDefinition) -> None:
    """Raise N8NValidationError if `definition` violates the registry's schema."""
    errors = []

    if not isinstance(definition.workflow_id, str) or not _WORKFLOW_ID_PATTERN.match(definition.workflow_id):
        errors.append(
            "workflow_id must be a non-empty lowercase slug (letters, digits, '_' or '-', "
            "starting with a letter or digit)"
        )
    if not isinstance(definition.name, str) or not definition.name.strip():
        errors.append("name must be a non-empty string")
    if not isinstance(definition.description, str):
        errors.append("description must be a string")

    if not isinstance(definition.webhook_url, str) or not definition.webhook_url.strip():
        errors.append("webhook_url must be a non-empty string")
    else:
        parsed = urllib.parse.urlparse(definition.webhook_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            errors.append("webhook_url must be a valid absolute http:// or https:// URL")

    if not definition.allowed_methods or not all(isinstance(m, str) for m in definition.allowed_methods):
        errors.append("allowed_methods must be a non-empty tuple of method strings")
    else:
        invalid_methods = sorted(m for m in definition.allowed_methods if m not in _VALID_METHODS)
        if invalid_methods:
            errors.append(
                f"allowed_methods contains unsupported method(s) {invalid_methods} "
                f"(must be one of {sorted(_VALID_METHODS)})"
            )

    if not isinstance(definition.enabled, bool):
        errors.append("enabled must be a bool")

    if (
        not isinstance(definition.timeout_seconds, (int, float))
        or isinstance(definition.timeout_seconds, bool)
        or definition.timeout_seconds <= 0
    ):
        errors.append("timeout_seconds must be a positive number")

    if definition.auth_header_env_var is not None and (
        not isinstance(definition.auth_header_env_var, str) or not definition.auth_header_env_var.strip()
    ):
        errors.append("auth_header_env_var must be None or a non-empty string")

    if errors:
        raise N8NValidationError(
            f"Invalid n8n workflow definition '{getattr(definition, 'workflow_id', '?')}': " + "; ".join(errors)
        )


def _send_webhook(definition: N8NWorkflowDefinition, method: str, payload: dict) -> N8NResult:
    """Perform the actual HTTP call. Never raises — every failure mode is
    translated into a structured N8NResult, and no exception's string
    representation (which can echo the request URL) is ever surfaced."""
    headers = {"Content-Type": "application/json"}
    if definition.auth_header_env_var:
        token = os.environ.get(definition.auth_header_env_var)
        if token:
            headers["Authorization"] = f"Bearer {token}"

    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        definition.webhook_url,
        data=body if method != "GET" else None,
        method=method,
        headers=headers,
    )

    try:
        with urllib.request.urlopen(request, timeout=definition.timeout_seconds) as response:
            status_code = response.getcode()
            raw = response.read()
    except urllib.error.HTTPError as exc:
        return N8NResult(
            workflow_id=definition.workflow_id, success=False, status_code=exc.code,
            error=f"n8n workflow '{definition.workflow_id}' returned HTTP {exc.code}",
            error_code="http_error",
        )
    except socket.timeout:
        return N8NResult(
            workflow_id=definition.workflow_id, success=False,
            error=f"Request to n8n workflow '{definition.workflow_id}' timed out after {definition.timeout_seconds}s",
            error_code="timeout",
        )
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, socket.timeout):
            return N8NResult(
                workflow_id=definition.workflow_id, success=False,
                error=f"Request to n8n workflow '{definition.workflow_id}' timed out after {definition.timeout_seconds}s",
                error_code="timeout",
            )
        return N8NResult(
            workflow_id=definition.workflow_id, success=False,
            error=f"Could not reach n8n workflow '{definition.workflow_id}' (network error)",
            error_code="network_error",
        )
    except Exception:
        return N8NResult(
            workflow_id=definition.workflow_id, success=False,
            error=f"Unexpected error calling n8n workflow '{definition.workflow_id}'",
            error_code="execution_error",
        )

    output = None
    if raw:
        try:
            output = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return N8NResult(
                workflow_id=definition.workflow_id, success=False, status_code=status_code,
                error=f"n8n workflow '{definition.workflow_id}' returned a non-JSON response",
                error_code="invalid_response",
            )
        if not _is_json_compatible(output):
            return N8NResult(
                workflow_id=definition.workflow_id, success=False, status_code=status_code,
                error=f"n8n workflow '{definition.workflow_id}' returned an unsupported response shape",
                error_code="invalid_response",
            )

    return N8NResult(workflow_id=definition.workflow_id, success=True, status_code=status_code, output=output)


class N8NRegistry:
    """In-memory allowlist of approved n8n workflows/webhooks. Validates on
    every registration; execute() is the single, fail-safe entry point for
    actually triggering a workflow."""

    def __init__(self, audit_store: Optional[AuditStore] = None):
        self._workflows: dict = {}
        self.audit_store = audit_store if audit_store is not None else default_audit_store

    def register(self, definition: N8NWorkflowDefinition, *, replace: bool = False) -> N8NWorkflowDefinition:
        validate_workflow(definition)
        if definition.workflow_id in self._workflows and not replace:
            raise DuplicateWorkflowError(
                f"n8n workflow '{definition.workflow_id}' is already registered (pass replace=True to overwrite)."
            )
        self._workflows[definition.workflow_id] = definition
        return definition

    def get(self, workflow_id: str) -> N8NWorkflowDefinition:
        try:
            return self._workflows[workflow_id]
        except KeyError:
            raise N8NNotFoundError(f"No n8n workflow registered with id '{workflow_id}'") from None

    def get_or_none(self, workflow_id: str) -> Optional[N8NWorkflowDefinition]:
        return self._workflows.get(workflow_id)

    def list_workflows(self, *, enabled: Optional[bool] = None) -> list:
        results = self._workflows.values()
        if enabled is not None:
            results = (w for w in results if w.enabled == enabled)
        return sorted(results, key=lambda w: w.workflow_id)

    def __len__(self) -> int:
        return len(self._workflows)

    def __contains__(self, workflow_id: str) -> bool:
        return workflow_id in self._workflows

    def execute(
        self,
        workflow_id: str,
        payload: Optional[dict] = None,
        *,
        method: Optional[str] = None,
        agent_id: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> N8NResult:
        """The single safe entry point for triggering an approved n8n
        webhook. Only a registered AND enabled workflow, called with one of
        its own allowed_methods and a JSON-compatible payload, ever reaches
        an actual HTTP request. Never raises.

        agent_id/request_id are optional audit context only — they don't
        affect execution and don't need to be supplied."""
        payload = payload if payload is not None else {}

        definition = self._workflows.get(workflow_id)
        if definition is None:
            result = N8NResult(
                workflow_id=workflow_id, success=False,
                error=f"Unknown n8n workflow '{workflow_id}'", error_code="unknown_workflow",
            )
        elif not definition.enabled:
            result = N8NResult(
                workflow_id=workflow_id, success=False,
                error=f"n8n workflow '{workflow_id}' is disabled", error_code="disabled",
            )
        else:
            resolved_method = (method or definition.allowed_methods[0]).upper()
            if resolved_method not in definition.allowed_methods:
                result = N8NResult(
                    workflow_id=workflow_id, success=False,
                    error=(
                        f"Method '{resolved_method}' is not allowed for workflow '{workflow_id}' "
                        f"(allowed: {', '.join(definition.allowed_methods)})"
                    ),
                    error_code="invalid_method",
                )
            elif not _is_json_compatible(payload):
                result = N8NResult(
                    workflow_id=workflow_id, success=False,
                    error="payload must be JSON-compatible (str/int/float/bool/None/list/dict only)",
                    error_code="invalid_payload",
                )
            else:
                result = _send_webhook(definition, resolved_method, payload)

        self._audit(workflow_id, agent_id, request_id, result)
        return result

    def _audit(self, workflow_id: str, agent_id, request_id, result: "N8NResult") -> None:
        try:
            self.audit_store.append(
                actor="n8n",
                action="n8n_execute",
                status="success" if result.success else (result.error_code or "failure"),
                request_id=request_id,
                agent_id=agent_id,
                tool_id=workflow_id,  # audit's schema has no separate workflow_id column
                metadata={"status_code": result.status_code},
            )
        except Exception as exc:  # AuditStore itself fails safe; this is a second net.
            warnings.warn(f"N8NRegistry: audit logging failed, continuing without it ({exc})", RuntimeWarning)


def make_n8n_tool(
    tool_id: str,
    name: str,
    description: str,
    workflow_id: str,
    *,
    n8n_registry: Optional[N8NRegistry] = None,
    method: Optional[str] = None,
    parameters: tuple = (),
) -> ToolDefinition:
    """Build a ToolDefinition whose handler triggers exactly one pre-approved
    n8n workflow via N8NRegistry.execute() — nothing else. The handler's
    signature only accepts the declared `parameters` as plain keyword
    arguments (validated by ToolRegistry.execute() itself, same as any other
    tool) and forwards them as the webhook payload; workflow_id is fixed at
    registration time in code, so no caller (LLM or otherwise) can redirect
    this tool to a different workflow or URL.

    The returned ToolDefinition is registered the normal way, through
    ToolRegistry.register() — this factory never registers anything itself,
    so there is exactly one path into the tool layer.
    """
    registry = n8n_registry if n8n_registry is not None else default_n8n_registry

    def _handler(**kwargs) -> dict:
        result = registry.execute(workflow_id, kwargs, method=method)
        return {
            "success": result.success,
            "status_code": result.status_code,
            "output": result.output,
            "error": result.error,
            "error_code": result.error_code,
        }

    return ToolDefinition(
        tool_id=tool_id,
        name=name,
        description=description,
        handler=_handler,
        parameters=parameters,
    )


default_n8n_registry = N8NRegistry()
