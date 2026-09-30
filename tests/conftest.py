"""
Shared test fixtures for tests/.

backend/llm.py's MOCK_MODE review scaffolding uses a module-level
itertools.count() (_review_call_count) to force "reject once, then approve"
so the rework loop is demonstrable without a live provider. That counter is
process-global, not per-invocation, so any two tests that both run the full
LangGraph workflow in MOCK_MODE (e.g. test_graph_workflow.py and the
dispatch tests in test_ai_executive.py) would otherwise share and exhaust
it across test order. Reset it before every test so each full-workflow test
gets its own fresh reject-then-approve cycle regardless of run order.
"""
import itertools
import tempfile
from pathlib import Path

import pytest

from backend import ai_executive, api_adapter, auth as auth_module, llm, n8n, tool_registry
from backend.audit import AuditStore
from backend.auth import AuthService, AuthStore
from backend.run_store import RunStore

# backend.main builds its API runtime (bound to default_run_store) the first
# time it is imported. Point that default at a throwaway file *before* any test
# can import it, so the suite never writes to the real data/runs.sqlite3.
api_adapter.default_run_store = RunStore(db_path=Path(tempfile.mkdtemp(prefix="iz_tests_")) / "runs.sqlite3")

# Same for the auth database: backend.main captures default_auth_service when it is
# first imported, so replace it (with a cheap scrypt cost) before that can happen.
auth_module.default_auth_service = AuthService(
    AuthStore(db_path=Path(tempfile.mkdtemp(prefix="iz_auth_tests_")) / "auth.sqlite3"), scrypt_n=16)


@pytest.fixture(autouse=True)
def _reset_mock_review_counter():
    llm._review_call_count = itertools.count(1)
    yield


@pytest.fixture(autouse=True)
def _isolated_audit_store(tmp_path, monkeypatch):
    """ToolRegistry/N8NRegistry/AIExecutive all default their audit_store to
    backend.audit.default_audit_store (a real file under data/) unless one
    is passed explicitly. Most tests construct bare ToolRegistry()/
    N8NRegistry() or rely on the pre-built default_tool_registry/
    default_n8n_registry/default_executive singletons without ever touching
    audit — so without this, every test run would write real rows into the
    project's shared data/audit.sqlite3. Point every default at a fresh,
    per-test file instead: patching the module-level name covers freshly
    constructed registries, and reassigning .audit_store on the singletons
    (already built at their module's import time, before this fixture ever
    runs) covers those specifically."""
    store = AuditStore(db_path=tmp_path / "audit_autouse.sqlite3")

    monkeypatch.setattr(tool_registry, "default_audit_store", store)
    monkeypatch.setattr(n8n, "default_audit_store", store)
    monkeypatch.setattr(ai_executive, "default_audit_store", store)

    monkeypatch.setattr(tool_registry.default_tool_registry, "audit_store", store)
    monkeypatch.setattr(n8n.default_n8n_registry, "audit_store", store)
    monkeypatch.setattr(ai_executive.default_executive, "audit_store", store)
    monkeypatch.setattr(auth_module.default_auth_service, "audit_store", store)

    yield


@pytest.fixture
def signed_in():
    """Factory: create a throwaway user of the given role in the app's default auth
    service and return Authorization headers for it. Users are removed afterwards."""
    service = auth_module.default_auth_service
    created = []

    def factory(role: str = "admin") -> dict:
        import uuid
        name = f"{role}-{uuid.uuid4().hex[:8]}"
        user = service.create_user(name, "correct horse battery", role)
        created.append(user.id)
        token, _, _ = service.login(name, "correct horse battery")
        return {"Authorization": f"Bearer {token}"}

    yield factory
    for user_id in created:
        service.store.delete_user(user_id)
