"""
Tests for backend/n8n.py: N8NWorkflowDefinition/N8NResult validation,
registration, allowlist enforcement (unknown/disabled/disallowed-method
all short-circuit before any network call), payload validation, mocked
HTTP outcomes (success, HTTP error, timeout, network error, bad response),
secret redaction, and the ToolRegistry integration via make_n8n_tool().

No live n8n instance is used anywhere — every HTTP outcome is produced by
monkeypatching backend.n8n.urllib.request.urlopen.
"""
import io
import json
import socket
import urllib.error

import pytest

from backend.n8n import (
    DuplicateWorkflowError,
    N8NConfigError,
    N8NNotFoundError,
    N8NRegistry,
    N8NValidationError,
    N8NWorkflowDefinition,
    default_n8n_registry,
    make_n8n_tool,
    validate_workflow,
)
from backend.tool_registry import ToolParameter, ToolRegistry


def make_workflow(**overrides) -> N8NWorkflowDefinition:
    fields = dict(
        workflow_id="test_workflow",
        name="Test Workflow",
        webhook_url="https://n8n.internal.example.com/webhook/abc123secret",
        allowed_methods=("POST",),
    )
    fields.update(overrides)
    return N8NWorkflowDefinition(**fields)


class _FakeResponse:
    def __init__(self, status, body: bytes):
        self._status = status
        self._body = body

    def getcode(self):
        return self._status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


# --- validation --------------------------------------------------------

def test_valid_workflow_passes_validation():
    validate_workflow(make_workflow())  # should not raise


@pytest.mark.parametrize("bad_id", ["", "Bad-ID", "has spaces", "_leading", "UPPER"])
def test_invalid_workflow_id_rejected(bad_id):
    with pytest.raises(N8NValidationError, match="workflow_id"):
        validate_workflow(make_workflow(workflow_id=bad_id))


def test_blank_name_rejected():
    with pytest.raises(N8NValidationError, match="name"):
        validate_workflow(make_workflow(name="   "))


@pytest.mark.parametrize("bad_url", ["", "not a url", "ftp://x.com/hook", "javascript:alert(1)"])
def test_invalid_webhook_url_rejected(bad_url):
    with pytest.raises(N8NValidationError, match="webhook_url"):
        validate_workflow(make_workflow(webhook_url=bad_url))


def test_invalid_allowed_method_rejected():
    with pytest.raises(N8NValidationError, match="allowed_methods"):
        validate_workflow(make_workflow(allowed_methods=("TRACE",)))


def test_empty_allowed_methods_rejected():
    with pytest.raises(N8NValidationError, match="allowed_methods"):
        validate_workflow(make_workflow(allowed_methods=()))


def test_non_positive_timeout_rejected():
    with pytest.raises(N8NValidationError, match="timeout_seconds"):
        validate_workflow(make_workflow(timeout_seconds=0))


def test_non_bool_enabled_rejected():
    with pytest.raises(N8NValidationError, match="enabled"):
        validate_workflow(make_workflow(enabled="yes"))


def test_blank_auth_header_env_var_rejected():
    with pytest.raises(N8NValidationError, match="auth_header_env_var"):
        validate_workflow(make_workflow(auth_header_env_var="   "))


def test_validation_error_never_echoes_the_webhook_url():
    try:
        validate_workflow(make_workflow(webhook_url="not-a-valid-url-with-secret-token-abc123"))
    except N8NValidationError as exc:
        assert "secret-token-abc123" not in str(exc)
    else:
        pytest.fail("expected N8NValidationError")


# --- registration / lookup / listing ---------------------------------------

def test_register_and_get_roundtrip():
    registry = N8NRegistry()
    wf = make_workflow()
    registry.register(wf)
    assert registry.get("test_workflow") is wf


def test_register_invalid_definition_raises_before_storing():
    registry = N8NRegistry()
    with pytest.raises(N8NValidationError):
        registry.register(make_workflow(workflow_id="Not Valid"))
    assert len(registry) == 0


def test_duplicate_registration_rejected_without_replace():
    registry = N8NRegistry()
    registry.register(make_workflow())
    with pytest.raises(DuplicateWorkflowError):
        registry.register(make_workflow(name="Different"))


def test_duplicate_registration_allowed_with_replace():
    registry = N8NRegistry()
    registry.register(make_workflow(name="Original"))
    registry.register(make_workflow(name="Updated"), replace=True)
    assert registry.get("test_workflow").name == "Updated"


def test_get_missing_workflow_raises():
    registry = N8NRegistry()
    with pytest.raises(N8NNotFoundError):
        registry.get("does-not-exist")


def test_get_or_none_missing_returns_none():
    registry = N8NRegistry()
    assert registry.get_or_none("does-not-exist") is None


def test_list_workflows_sorted_and_filterable_by_enabled():
    registry = N8NRegistry()
    registry.register(make_workflow(workflow_id="b_flow", enabled=False))
    registry.register(make_workflow(workflow_id="a_flow", enabled=True))
    assert [w.workflow_id for w in registry.list_workflows()] == ["a_flow", "b_flow"]
    assert [w.workflow_id for w in registry.list_workflows(enabled=True)] == ["a_flow"]
    assert [w.workflow_id for w in registry.list_workflows(enabled=False)] == ["b_flow"]


# --- from_env() --------------------------------------------------------

def test_from_env_reads_webhook_url_from_environment(monkeypatch):
    monkeypatch.setenv("MY_WEBHOOK_URL", "https://n8n.example.com/webhook/xyz")
    wf = N8NWorkflowDefinition.from_env("wf1", "WF1", url_env_var="MY_WEBHOOK_URL")
    assert wf.webhook_url == "https://n8n.example.com/webhook/xyz"


def test_from_env_raises_when_env_var_missing(monkeypatch):
    monkeypatch.delenv("MISSING_WEBHOOK_URL", raising=False)
    with pytest.raises(N8NConfigError):
        N8NWorkflowDefinition.from_env("wf1", "WF1", url_env_var="MISSING_WEBHOOK_URL")


# --- execute(): allowlist enforcement, before any network call -------------

def test_execute_unknown_workflow_fails_safe_without_network(monkeypatch):
    registry = N8NRegistry()
    called = []
    monkeypatch.setattr("backend.n8n.urllib.request.urlopen", lambda *a, **k: called.append(1))

    result = registry.execute("nope")

    assert result.success is False
    assert result.error_code == "unknown_workflow"
    assert called == []


def test_execute_disabled_workflow_fails_safe_without_network(monkeypatch):
    registry = N8NRegistry()
    registry.register(make_workflow(enabled=False))
    called = []
    monkeypatch.setattr("backend.n8n.urllib.request.urlopen", lambda *a, **k: called.append(1))

    result = registry.execute("test_workflow")

    assert result.success is False
    assert result.error_code == "disabled"
    assert called == []


def test_execute_disallowed_method_fails_safe_without_network(monkeypatch):
    registry = N8NRegistry()
    registry.register(make_workflow(allowed_methods=("POST",)))
    called = []
    monkeypatch.setattr("backend.n8n.urllib.request.urlopen", lambda *a, **k: called.append(1))

    result = registry.execute("test_workflow", method="DELETE")

    assert result.success is False
    assert result.error_code == "invalid_method"
    assert called == []


def test_execute_invalid_payload_fails_safe_without_network(monkeypatch):
    registry = N8NRegistry()
    registry.register(make_workflow())
    called = []
    monkeypatch.setattr("backend.n8n.urllib.request.urlopen", lambda *a, **k: called.append(1))

    result = registry.execute("test_workflow", {"bad": object()})

    assert result.success is False
    assert result.error_code == "invalid_payload"
    assert called == []


# --- execute(): mocked HTTP outcomes ----------------------------------------

def test_execute_success_returns_parsed_json_output(monkeypatch):
    registry = N8NRegistry()
    registry.register(make_workflow())
    monkeypatch.setattr(
        "backend.n8n.urllib.request.urlopen",
        lambda req, timeout=None: _FakeResponse(200, json.dumps({"ok": True, "id": 42}).encode()),
    )

    result = registry.execute("test_workflow", {"x": 1})

    assert result.success is True
    assert result.status_code == 200
    assert result.output == {"ok": True, "id": 42}


def test_execute_http_error_fails_safe(monkeypatch):
    registry = N8NRegistry()
    registry.register(make_workflow())

    def raise_http_error(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 500, "Internal Server Error", None, io.BytesIO(b""))

    monkeypatch.setattr("backend.n8n.urllib.request.urlopen", raise_http_error)

    result = registry.execute("test_workflow")

    assert result.success is False
    assert result.status_code == 500
    assert result.error_code == "http_error"


def test_execute_timeout_fails_safe(monkeypatch):
    registry = N8NRegistry()
    registry.register(make_workflow(timeout_seconds=2))

    def raise_timeout(req, timeout=None):
        raise socket.timeout("timed out")

    monkeypatch.setattr("backend.n8n.urllib.request.urlopen", raise_timeout)

    result = registry.execute("test_workflow")

    assert result.success is False
    assert result.error_code == "timeout"


def test_execute_urlerror_wrapping_timeout_fails_safe_as_timeout(monkeypatch):
    registry = N8NRegistry()
    registry.register(make_workflow())

    def raise_url_timeout(req, timeout=None):
        raise urllib.error.URLError(socket.timeout("timed out"))

    monkeypatch.setattr("backend.n8n.urllib.request.urlopen", raise_url_timeout)

    result = registry.execute("test_workflow")

    assert result.error_code == "timeout"


def test_execute_network_error_fails_safe(monkeypatch):
    registry = N8NRegistry()
    registry.register(make_workflow())

    def raise_network_error(req, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("backend.n8n.urllib.request.urlopen", raise_network_error)

    result = registry.execute("test_workflow")

    assert result.success is False
    assert result.error_code == "network_error"


def test_execute_non_json_response_fails_safe(monkeypatch):
    registry = N8NRegistry()
    registry.register(make_workflow())
    monkeypatch.setattr(
        "backend.n8n.urllib.request.urlopen",
        lambda req, timeout=None: _FakeResponse(200, b"not json at all"),
    )

    result = registry.execute("test_workflow")

    assert result.success is False
    assert result.error_code == "invalid_response"


def test_execute_empty_response_body_is_success_with_none_output(monkeypatch):
    registry = N8NRegistry()
    registry.register(make_workflow())
    monkeypatch.setattr(
        "backend.n8n.urllib.request.urlopen",
        lambda req, timeout=None: _FakeResponse(204, b""),
    )

    result = registry.execute("test_workflow")

    assert result.success is True
    assert result.output is None


def test_execute_uses_first_allowed_method_by_default(monkeypatch):
    registry = N8NRegistry()
    registry.register(make_workflow(allowed_methods=("PUT",)))
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["method"] = req.get_method()
        return _FakeResponse(200, b"{}")

    monkeypatch.setattr("backend.n8n.urllib.request.urlopen", fake_urlopen)
    registry.execute("test_workflow")

    assert captured["method"] == "PUT"


# --- secret handling ---------------------------------------------------

def test_auth_header_uses_env_var_value_but_result_never_contains_it(monkeypatch):
    monkeypatch.setenv("MY_SECRET_TOKEN", "super-secret-value")
    registry = N8NRegistry()
    registry.register(make_workflow(auth_header_env_var="MY_SECRET_TOKEN"))
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["headers"] = dict(req.header_items())
        return _FakeResponse(200, b"{}")

    monkeypatch.setattr("backend.n8n.urllib.request.urlopen", fake_urlopen)

    result = registry.execute("test_workflow")

    assert captured["headers"].get("Authorization") == "Bearer super-secret-value"
    assert "super-secret-value" not in repr(result)
    assert "super-secret-value" not in str(result)


def test_missing_auth_env_var_sends_no_authorization_header(monkeypatch):
    monkeypatch.delenv("MISSING_TOKEN_VAR", raising=False)
    registry = N8NRegistry()
    registry.register(make_workflow(auth_header_env_var="MISSING_TOKEN_VAR"))
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["headers"] = dict(req.header_items())
        return _FakeResponse(200, b"{}")

    monkeypatch.setattr("backend.n8n.urllib.request.urlopen", fake_urlopen)
    registry.execute("test_workflow")

    assert "Authorization" not in captured["headers"]


def test_error_results_never_contain_the_webhook_url_or_its_secret(monkeypatch):
    registry = N8NRegistry()
    registry.register(make_workflow(webhook_url="https://n8n.example.com/webhook/super-secret-path-id"))

    def raise_network_error(req, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("backend.n8n.urllib.request.urlopen", raise_network_error)

    result = registry.execute("test_workflow")

    assert "super-secret-path-id" not in repr(result)
    assert "super-secret-path-id" not in (result.error or "")


def test_redacted_url_hides_path_and_query():
    wf = make_workflow(webhook_url="https://n8n.example.com/webhook/super-secret-id?token=abc123")
    assert wf.redacted_url == "https://n8n.example.com/***"
    assert "super-secret-id" not in wf.redacted_url
    assert "abc123" not in wf.redacted_url


# --- ToolRegistry integration ------------------------------------------

def test_make_n8n_tool_registers_through_tool_registry_and_executes(monkeypatch):
    n8n_registry = N8NRegistry()
    n8n_registry.register(make_workflow())
    tool_registry = ToolRegistry()
    tool_def = make_n8n_tool(
        "trigger_test_workflow", "Trigger Test Workflow", "Triggers the test n8n workflow.",
        "test_workflow", n8n_registry=n8n_registry,
        parameters=(ToolParameter(name="amount", type="float", required=True),),
    )
    tool_registry.register(tool_def)

    monkeypatch.setattr(
        "backend.n8n.urllib.request.urlopen",
        lambda req, timeout=None: _FakeResponse(200, json.dumps({"invoiced": True}).encode()),
    )

    result = tool_registry.execute("trigger_test_workflow", {"amount": 12.5})

    assert result.success is True
    assert result.output["success"] is True
    assert result.output["output"] == {"invoiced": True}


def test_n8n_tool_cannot_be_redirected_to_a_different_workflow():
    """The handler's only parameters are payload fields — there is no way
    for a caller (including an LLM-selected tool call) to pass a different
    workflow_id or URL; it's fixed in code at registration time."""
    n8n_registry = N8NRegistry()
    n8n_registry.register(make_workflow(workflow_id="approved_flow"))
    n8n_registry.register(make_workflow(
        workflow_id="other_flow", webhook_url="https://n8n.example.com/webhook/other",
    ))
    tool_registry = ToolRegistry()
    tool_registry.register(make_n8n_tool(
        "trigger_approved", "Trigger Approved", "desc", "approved_flow", n8n_registry=n8n_registry,
    ))

    # ToolRegistry's own input validation rejects this: the handler declares
    # no "workflow_id" parameter, so there is no way to redirect it.
    result = tool_registry.execute("trigger_approved", {"workflow_id": "other_flow"})

    assert result.success is False
    assert result.error_code == "invalid_input"


def test_n8n_tool_fails_safe_when_workflow_unavailable(monkeypatch):
    n8n_registry = N8NRegistry()
    n8n_registry.register(make_workflow())
    tool_registry = ToolRegistry()
    tool_registry.register(make_n8n_tool("trigger", "Trigger", "desc", "test_workflow", n8n_registry=n8n_registry))

    def raise_network_error(req, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("backend.n8n.urllib.request.urlopen", raise_network_error)

    result = tool_registry.execute("trigger", {})

    assert result.success is True  # the tool call itself completed structurally
    assert result.output["success"] is False
    assert result.output["error_code"] == "network_error"


def test_default_n8n_registry_starts_empty():
    # Explicit allowlist: nothing is approved until a developer registers it.
    assert len(default_n8n_registry.list_workflows()) == 0
