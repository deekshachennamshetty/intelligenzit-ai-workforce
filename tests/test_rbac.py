"""
Route-protection matrix: which roles may call which /api/* (and legacy) routes.

    public    /health, /api/auth/login, /api/auth/status, docs, static UI files
    viewer    every read route
    operator  viewer + POST /api/workflows, POST .../decision, legacy /submit
    admin     operator + /api/governance, /api/audit, /api/users*

Runs on an isolated app (temp stores, MOCK_MODE, synchronous runtime) so no
real data is touched; the assembled backend.main app is checked separately.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.ai_executive import AIExecutive
from backend.api_adapter import AdapterRuntime, build_router
from backend.audit import AuditStore
from backend.auth import AuthService, AuthStore
from backend.auth_api import build_auth_router
from backend.knowledge_base import KnowledgeBase
from backend.memory import MemoryStore
from backend.run_store import RunStore

PASSWORD = "correct horse battery"
OBJECTIVE = "An organisation wants a technology recommendation for modernising a core process."

VIEWER_READS = [
    "/api/config", "/api/graph", "/api/workflows", "/api/workflows/nope", "/api/workflows/nope/status",
    "/api/workflows/nope/result", "/api/workflows/nope/research", "/api/research", "/api/agents",
    "/api/agents/solution", "/api/memory", "/api/metrics", "/api/dashboard",
]
ADMIN_READS = ["/api/governance", "/api/audit", "/api/users"]
OPERATOR_WRITES = [
    ("POST", "/api/workflows", {"objective": OBJECTIVE}),
    ("POST", "/api/workflows/nope/decision", {"decision": "approve"}),
]


@pytest.fixture(autouse=True)
def mock_mode(monkeypatch):
    monkeypatch.setenv("MOCK_MODE", "1")


class Stack:
    def __init__(self, tmp_path, enabled=True):
        self.audit = AuditStore(db_path=tmp_path / "audit.sqlite3")
        self.service = AuthService(AuthStore(db_path=tmp_path / "auth.sqlite3"), audit_store=self.audit,
                                   scrypt_n=16, enabled=enabled)
        executive = AIExecutive(
            memory=MemoryStore(db_path=tmp_path / "mem.sqlite3"),
            knowledge_base=KnowledgeBase(db_path=tmp_path / "kb.sqlite3"), audit_store=self.audit)
        self.runtime = AdapterRuntime(executive, spawn=lambda fn: fn(),
                                      run_store=RunStore(db_path=tmp_path / "runs.sqlite3"))
        app = FastAPI()
        app.include_router(build_auth_router(self.service))
        app.include_router(build_router(self.runtime, auth=self.service))
        self.client = TestClient(app)

    def headers(self, role: str) -> dict:
        name = f"{role}-user"
        if self.service.store.get_credentials(name) is None:
            self.service.create_user(name, PASSWORD, role)
        token = self.client.post("/api/auth/login", json={"username": name, "password": PASSWORD}).json()["token"]
        return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def stack(tmp_path):
    return Stack(tmp_path)


def ok(response) -> bool:
    return response.status_code not in (401, 403)


# --- public surface -------------------------------------------------------------------

def test_health_is_public_and_reports_auth_state(stack):
    body = stack.client.get("/health").json()
    assert stack.client.get("/health").status_code == 200
    assert body["auth"] == "enabled" and body["status"] in ("ok", "degraded")


def test_login_and_status_are_public(stack):
    assert stack.client.get("/api/auth/status").status_code == 200
    bad = stack.client.post("/api/auth/login", json={"username": "x", "password": "y"})
    assert bad.status_code == 401 and bad.json()["detail"] == "Invalid username or password."


# --- anonymous ----------------------------------------------------------------------------

@pytest.mark.parametrize("path", VIEWER_READS + ADMIN_READS)
def test_anonymous_reads_are_refused_with_401(stack, path):
    response = stack.client.get(path)
    assert response.status_code == 401 and response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("method,path,body", OPERATOR_WRITES)
def test_anonymous_writes_are_refused_with_401(stack, method, path, body):
    assert stack.client.request(method, path, json=body).status_code == 401
    assert stack.runtime.list_payload(10, 0, None)["total"] == 0


# --- viewer -----------------------------------------------------------------------------------

@pytest.mark.parametrize("path", VIEWER_READS)
def test_viewer_may_read(stack, path):
    assert ok(stack.client.get(path, headers=stack.headers("viewer")))


@pytest.mark.parametrize("path", ADMIN_READS)
def test_viewer_may_not_read_admin_routes(stack, path):
    assert stack.client.get(path, headers=stack.headers("viewer")).status_code == 403


@pytest.mark.parametrize("method,path,body", OPERATOR_WRITES)
def test_viewer_may_not_write(stack, method, path, body):
    assert stack.client.request(method, path, json=body, headers=stack.headers("viewer")).status_code == 403
    assert stack.runtime.list_payload(10, 0, None)["total"] == 0  # nothing was started


# --- operator ------------------------------------------------------------------------------------

@pytest.mark.parametrize("path", VIEWER_READS)
def test_operator_may_read(stack, path):
    assert ok(stack.client.get(path, headers=stack.headers("operator")))


def test_operator_may_create_and_run_workflows(stack):
    headers = stack.headers("operator")
    response = stack.client.post("/api/workflows", json={"objective": OBJECTIVE}, headers=headers)
    assert response.status_code == 200
    request_id = response.json()["request_id"]
    status = stack.client.get(f"/api/workflows/{request_id}/status", headers=headers).json()
    assert status["status"] == "completed"


def test_operator_may_reach_the_decision_route(stack):
    headers = stack.headers("operator")
    request_id = stack.client.post("/api/workflows", json={"objective": OBJECTIVE}, headers=headers).json()["request_id"]
    # authorised, so it reaches the handler (which reports approval is not enabled)
    assert stack.client.post(f"/api/workflows/{request_id}/decision", json={"decision": "approve"},
                             headers=headers).status_code == 409


@pytest.mark.parametrize("path", ADMIN_READS)
def test_operator_may_not_read_admin_routes(stack, path):
    assert stack.client.get(path, headers=stack.headers("operator")).status_code == 403


# --- admin ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("path", VIEWER_READS + ADMIN_READS)
def test_admin_may_read_everything(stack, path):
    assert ok(stack.client.get(path, headers=stack.headers("admin")))


def test_admin_may_write(stack):
    headers = stack.headers("admin")
    assert stack.client.post("/api/workflows", json={"objective": OBJECTIVE}, headers=headers).status_code == 200


# --- attribution and audit ------------------------------------------------------------------------------------

def test_runs_are_attributed_to_the_signed_in_user_not_a_client_supplied_name(stack):
    headers = stack.headers("operator")
    request_id = stack.client.post(
        "/api/workflows", json={"objective": OBJECTIVE, "submitted_by": "someone-else"},
        headers=headers).json()["request_id"]
    assert stack.runtime._get(request_id)["submitted_by"] == "operator-user"
    assert stack.runtime.run_store.get(request_id)["submitted_by"] == "operator-user"


def test_denied_access_is_audited_without_secrets(stack):
    headers = stack.headers("viewer")
    stack.client.post("/api/workflows", json={"objective": OBJECTIVE}, headers=headers)

    denied = [e for e in stack.audit.recent(50) if e.action == "access_denied"]
    assert len(denied) == 1
    assert denied[0].metadata == {"username": "viewer-user", "role": "viewer", "required_role": "operator",
                                  "method": "POST", "path": "/api/workflows"}
    assert headers["Authorization"].split()[1] not in " ".join(str(e) for e in stack.audit.recent(50))


# --- session lifecycle affects access immediately ------------------------------------------------------------------------

def test_demoting_a_user_ends_their_session_immediately(stack):
    stack.headers("admin")  # keep an admin around
    headers = stack.headers("operator")
    assert stack.client.post("/api/workflows", json={"objective": OBJECTIVE}, headers=headers).status_code == 200
    target = stack.service.store.get_credentials("operator-user")["id"]
    stack.service.update_user(target, role="viewer")
    assert stack.client.get("/api/agents", headers=headers).status_code == 401


def test_disabling_a_user_blocks_their_existing_token(stack):
    stack.headers("admin")
    headers = stack.headers("viewer")
    target = stack.service.store.get_credentials("viewer-user")["id"]
    stack.service.update_user(target, enabled=False)
    assert stack.client.get("/api/agents", headers=headers).status_code == 401


def test_logout_blocks_further_use_of_the_token(stack):
    headers = stack.headers("viewer")
    assert stack.client.get("/api/agents", headers=headers).status_code == 200
    stack.client.post("/api/auth/logout", headers=headers)
    assert stack.client.get("/api/agents", headers=headers).status_code == 401


def test_the_x_api_key_alias_works_on_protected_routes(stack):
    token = stack.headers("viewer")["Authorization"].split()[1]
    assert stack.client.get("/api/agents", headers={"X-API-Key": token}).status_code == 200


# --- what the UI is told about protection ------------------------------------------------------------------------------------

def test_config_and_governance_report_that_authentication_is_enforced(stack):
    assert stack.client.get("/api/config", headers=stack.headers("viewer")).json()["api_key_required"] is True
    governance = stack.client.get("/api/governance", headers=stack.headers("admin")).json()
    controls = {c["control"]: c["status"] for c in governance["security"]}
    assert controls["API authentication"] == "enforced"
    assert controls["Role-based access control"] == "enforced"
    assert governance["policy"]["api_key_required"] is True


# --- enforcement switched off / router built without auth -------------------------------------------------------------------------

def test_disabled_auth_leaves_routes_open_and_says_so(tmp_path):
    open_stack = Stack(tmp_path, enabled=False)
    assert open_stack.client.get("/api/agents").status_code == 200
    assert open_stack.client.get("/api/governance").status_code == 200
    assert open_stack.client.post("/api/workflows", json={"objective": OBJECTIVE}).status_code == 200
    assert open_stack.client.get("/health").json()["auth"] == "disabled"
    controls = {c["control"]: c["status"] for c in open_stack.client.get("/api/governance").json()["security"]}
    assert controls["API authentication"] == "not_implemented"


def test_router_built_without_an_auth_service_is_unprotected(tmp_path):
    executive = AIExecutive(memory=MemoryStore(db_path=tmp_path / "m.sqlite3"),
                            knowledge_base=KnowledgeBase(db_path=tmp_path / "k.sqlite3"),
                            audit_store=AuditStore(db_path=tmp_path / "a.sqlite3"))
    app = FastAPI()
    app.include_router(build_router(AdapterRuntime(executive, spawn=lambda fn: fn())))
    assert TestClient(app).get("/api/agents").status_code == 200


def test_protected_routes_fail_closed_when_the_auth_database_is_down(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    with pytest.warns(RuntimeWarning):
        broken = AuthStore(db_path=blocker / "sub" / "auth.sqlite3")
    audit = AuditStore(db_path=tmp_path / "audit.sqlite3")
    service = AuthService(broken, audit_store=audit, scrypt_n=16)
    executive = AIExecutive(memory=MemoryStore(db_path=tmp_path / "m.sqlite3"),
                            knowledge_base=KnowledgeBase(db_path=tmp_path / "k.sqlite3"), audit_store=audit)
    app = FastAPI()
    app.include_router(build_router(AdapterRuntime(executive, spawn=lambda fn: fn()), auth=service))
    client = TestClient(app)

    assert client.get("/api/agents", headers={"Authorization": "Bearer x"}).status_code == 503
    assert client.get("/api/agents").status_code == 503
    assert client.get("/health").status_code == 200  # still public


# --- the assembled application (backend.main) --------------------------------------------------------------------------------------

def test_main_app_public_surface(signed_in):
    from backend.main import app
    client = TestClient(app)
    assert client.get("/health").status_code == 200
    assert client.get("/api/auth/status").status_code == 200
    assert client.get("/api/docs").status_code == 200
    for page in ("/", "/login.html", "/dashboard.html", "/users.html", "/assets/css/tokens.css",
                 "/assets/pages/page-login.js"):
        assert client.get(page).status_code == 200, page


@pytest.mark.parametrize("path", VIEWER_READS + ADMIN_READS + ["/history", "/status/x", "/result/x"])
def test_main_app_data_routes_require_a_session(path):
    from backend.main import app
    assert TestClient(app).get(path).status_code == 401


def test_main_app_role_boundaries(signed_in):
    from backend.main import app
    client = TestClient(app)
    viewer, operator, admin = signed_in("viewer"), signed_in("operator"), signed_in("admin")

    assert client.get("/api/agents", headers=viewer).status_code == 200
    assert client.get("/api/governance", headers=viewer).status_code == 403
    assert client.get("/api/governance", headers=admin).status_code == 200
    assert client.get("/api/users", headers=operator).status_code == 403
    assert client.get("/api/users", headers=admin).status_code == 200
    assert client.post("/api/workflows", json={"objective": OBJECTIVE}, headers=viewer).status_code == 403


def test_main_app_legacy_routes_are_protected_too(monkeypatch, signed_in):
    from backend import main

    class StubGraph:
        def invoke(self, state):
            return {**state, "status": "complete", "final_report": "r", "log": []}

    monkeypatch.setattr(main, "workflow_graph", StubGraph())
    client = TestClient(main.app)
    viewer, operator = signed_in("viewer"), signed_in("operator")

    assert client.post("/submit", json={"text": "hello there"}).status_code == 401
    assert client.post("/submit", json={"text": "hello there"}, headers=viewer).status_code == 403
    assert client.post("/submit", json={"text": "hello there"}, headers=operator).status_code == 200
    assert client.get("/history", headers=viewer).status_code == 200
