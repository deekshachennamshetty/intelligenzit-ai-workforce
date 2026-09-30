"""
Tests for the provider abstraction in backend/llm.py: provider selection
(ollama default, anthropic opt-in via env), dispatch to the right client,
JSON parsing/retry behavior, and MOCK_MODE bypass. Real network/client
calls are stubbed via fake modules injected into sys.modules so these tests
run without a live Ollama server or Anthropic API key.
"""
import sys
import types

import pytest

from backend import llm


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ("MOCK_MODE", "LLM_PROVIDER", "OLLAMA_HOST", "OLLAMA_MODEL",
                "ANTHROPIC_MODEL", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    llm._anthropic_client = None
    yield
    llm._anthropic_client = None


def _fake_ollama_module(handle_chat):
    class FakeClient:
        def __init__(self, host):
            self.host = host

        def chat(self, model, messages, options=None, format=None):
            return handle_chat(model=model, messages=messages, options=options, format=format)

    return types.SimpleNamespace(Client=FakeClient)


def _fake_anthropic_module(response_text):
    class FakeBlock:
        type = "text"
        text = response_text

    class FakeMessages:
        def create(self, model, max_tokens, system, messages):
            return types.SimpleNamespace(content=[FakeBlock()])

    class FakeClient:
        def __init__(self, api_key):
            self.api_key = api_key
            self.messages = FakeMessages()

    return types.SimpleNamespace(Anthropic=FakeClient)


def test_default_provider_is_ollama():
    assert llm._provider() == "ollama"


def test_provider_overridable_via_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    assert llm._provider() == "anthropic"


def test_unknown_provider_raises(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "bogus")
    with pytest.raises(RuntimeError, match="Unknown LLM_PROVIDER"):
        llm.call_llm("system", "user")


def test_mock_mode_bypasses_provider(monkeypatch):
    monkeypatch.setenv("MOCK_MODE", "1")
    monkeypatch.setenv("LLM_PROVIDER", "bogus")  # must not even be consulted
    result = llm.call_llm("You are the Research agent.", "hi")
    assert "MOCK RESEARCH FINDINGS" in result


def test_call_llm_dispatches_to_ollama_by_default(monkeypatch):
    captured = {}

    def handle_chat(**kwargs):
        captured.update(kwargs)
        return {"message": {"content": "hello from ollama"}}

    monkeypatch.setitem(sys.modules, "ollama", _fake_ollama_module(handle_chat))
    monkeypatch.setenv("OLLAMA_HOST", "http://test-host:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "test-model")

    out = llm.call_llm("system prompt", "user prompt")

    assert out == "hello from ollama"
    assert captured["model"] == "test-model"
    assert captured["messages"] == [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "user prompt"},
    ]


def test_call_llm_dispatches_to_anthropic_when_selected(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setitem(sys.modules, "anthropic", _fake_anthropic_module("hello from anthropic"))

    out = llm.call_llm("system", "user")

    assert out == "hello from anthropic"


def test_anthropic_requires_api_key(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setitem(sys.modules, "anthropic", _fake_anthropic_module("unused"))
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        llm.call_llm("system", "user")


def test_call_llm_json_parses_clean_json(monkeypatch):
    monkeypatch.setitem(
        sys.modules, "ollama",
        _fake_ollama_module(lambda **_: {"message": {"content": '{"decision": "approve"}'}}),
    )
    assert llm.call_llm_json("system", "user") == {"decision": "approve"}


def test_call_llm_json_strips_code_fences(monkeypatch):
    monkeypatch.setitem(
        sys.modules, "ollama",
        _fake_ollama_module(lambda **_: {"message": {"content": '```json\n{"a": 1}\n```'}}),
    )
    assert llm.call_llm_json("system", "user") == {"a": 1}


def test_call_llm_json_requests_ollama_json_format(monkeypatch):
    captured = {}

    def handle_chat(**kwargs):
        captured.update(kwargs)
        return {"message": {"content": "{}"}}

    monkeypatch.setitem(sys.modules, "ollama", _fake_ollama_module(handle_chat))
    llm.call_llm_json("system", "user")
    assert captured["format"] == "json"


def test_call_llm_json_retries_once_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def handle_chat(**_):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"message": {"content": "not json at all"}}
        return {"message": {"content": '{"ok": true}'}}

    monkeypatch.setitem(sys.modules, "ollama", _fake_ollama_module(handle_chat))
    result = llm.call_llm_json("system", "user")

    assert result == {"ok": True}
    assert calls["n"] == 2


def test_call_llm_json_fails_soft_after_exhausting_retry(monkeypatch):
    monkeypatch.setitem(
        sys.modules, "ollama",
        _fake_ollama_module(lambda **_: {"message": {"content": "still not json"}}),
    )
    result = llm.call_llm_json("system", "user")

    assert result["_parse_error"] is True
    assert result["raw"] == "still not json"


def test_mock_mode_bypasses_provider_for_json(monkeypatch):
    monkeypatch.setenv("MOCK_MODE", "1")
    result = llm.call_llm_json("You are the Orchestrator agent.", "hi")
    assert result["request_type"] == "technology_recommendation"
