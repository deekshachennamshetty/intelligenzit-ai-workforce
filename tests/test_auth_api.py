"""
Tests for backend/auth_api.py: login/logout/me/status/password and the admin
user-management routes, including validation, self-protection, secret hygiene,
disabled-auth mode and fail-closed behaviour.
"""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.audit import AuditStore
from backend.auth import AuthService, AuthStore
from backend.auth_api import build_auth_router

PASSWORD = "correct horse battery"


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


def make(tmp_path, *, enabled=True, **kwargs):
    clock = Clock()
    service = AuthService(
        AuthStore(db_path=tmp_path / "auth.sqlite3"),
        audit_store=AuditStore(db_path=tmp_path / "audit.sqlite3"),
        scrypt_n=16, clock=clock, enabled=enabled, **kwargs)
    app = FastAPI()
    app.include_router(build_auth_router(service))
    return TestClient(app), service, clock


def sign_in(client, service, name, role, password=PASSWORD) -> dict:
    service.create_user(name, password, role)
    token = client.post("/api/auth/login", json={"username": name, "password": password}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def env(tmp_path):
    client, service, clock = make(tmp_path)
    return client, service, clock


# --- GET /api/auth/status -----------------------------------------------------

def test_status_is_public_and_reflects_whether_users_exist(env):
    client, service, _ = env
    assert client.get("/api/auth/status").json() == {
        "auth_enabled": True, "users_configured": False, "roles": ["viewer", "operator", "admin"]}
    service.create_user("alice", PASSWORD, "viewer")
    assert client.get("/api/auth/status").json()["users_configured"] is True


# --- POST /api/auth/login ---------------------------------------------------------

def test_login_success_returns_a_bearer_token_and_the_public_user(env):
    client, service, _ = env
    service.create_user("alice", PASSWORD, "operator", "Alice")
    body = client.post("/api/auth/login", json={"username": "Alice", "password": PASSWORD}).json()

    assert body["token_type"] == "bearer" and len(body["token"]) >= 32 and body["expires_at"]
    assert body["user"]["username"] == "alice" and body["user"]["role"] == "operator"
    assert "password" not in json.dumps(body["user"]) and "hash" not in json.dumps(body["user"])


def test_login_failure_is_a_uniform_401(env):
    client, service, _ = env
    service.create_user("alice", PASSWORD, "viewer")
    wrong = client.post("/api/auth/login", json={"username": "alice", "password": "nope nope nope"})
    unknown = client.post("/api/auth/login", json={"username": "ghost", "password": "nope nope nope"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json() == {"detail": "Invalid username or password."}


def test_login_locks_after_repeated_failures_with_429(tmp_path):
    client, service, clock = make(tmp_path, max_failed_attempts=3)
    service.create_user("alice", PASSWORD, "viewer")
    for _ in range(3):
        assert client.post("/api/auth/login", json={"username": "alice", "password": "bad bad bad"}).status_code == 401
    locked = client.post("/api/auth/login", json={"username": "alice", "password": PASSWORD})
    assert locked.status_code == 429
    clock.now += 600
    assert client.post("/api/auth/login", json={"username": "alice", "password": PASSWORD}).status_code == 200


@pytest.mark.parametrize("payload", [{}, {"username": "alice"}, {"password": "x"}, {"username": "x" * 65, "password": "x"}])
def test_login_validates_the_body(env, payload):
    client, _, _ = env
    assert client.post("/api/auth/login", json=payload).status_code == 422


def test_login_when_auth_is_disabled_is_a_409(tmp_path):
    client, _, _ = make(tmp_path, enabled=False)
    assert client.post("/api/auth/login", json={"username": "a", "password": "b"}).status_code == 409


# --- GET /api/auth/me -----------------------------------------------------------------

def test_me_requires_a_valid_session(env):
    client, service, clock = env
    headers = sign_in(client, service, "alice", "viewer")
    body = client.get("/api/auth/me", headers=headers).json()
    assert body["user"]["username"] == "alice" and body["auth_enabled"] is True

    missing = client.get("/api/auth/me")
    assert missing.status_code == 401 and missing.headers["www-authenticate"] == "Bearer"
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer nonsense"}).status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": "Basic abc"}).status_code == 401


def test_the_x_api_key_header_is_accepted_as_a_session_token(env):
    client, service, _ = env
    token = sign_in(client, service, "alice", "viewer")["Authorization"].split()[1]
    assert client.get("/api/auth/me", headers={"X-API-Key": token}).status_code == 200


def test_expired_sessions_are_rejected(tmp_path):
    client, service, clock = make(tmp_path, session_hours=1)
    headers = sign_in(client, service, "alice", "viewer")
    clock.now += 3601
    assert client.get("/api/auth/me", headers=headers).status_code == 401


def test_me_when_auth_is_disabled_reports_a_local_admin(tmp_path):
    client, _, _ = make(tmp_path, enabled=False)
    body = client.get("/api/auth/me").json()
    assert body["auth_enabled"] is False and body["user"]["role"] == "admin"
    assert client.get("/api/auth/status").json()["auth_enabled"] is False


# --- POST /api/auth/logout --------------------------------------------------------------------

def test_logout_revokes_the_token(env):
    client, service, _ = env
    headers = sign_in(client, service, "alice", "viewer")
    assert client.post("/api/auth/logout", headers=headers).json() == {"status": "signed_out"}
    assert client.get("/api/auth/me", headers=headers).status_code == 401


def test_logout_requires_a_session(env):
    client, _, _ = env
    assert client.post("/api/auth/logout").status_code == 401


# --- POST /api/auth/password ----------------------------------------------------------------------

def test_change_password_flow(env):
    client, service, _ = env
    service.create_user("alice", PASSWORD, "viewer")
    tokens = [client.post("/api/auth/login", json={"username": "alice", "password": PASSWORD}).json()["token"]
              for _ in range(2)]
    current, other = ({"Authorization": f"Bearer {t}"} for t in tokens)

    wrong = client.post("/api/auth/password", headers=current,
                        json={"current_password": "not it at all", "new_password": "a new good password"})
    assert wrong.status_code == 422 and client.get("/api/auth/me", headers=current).status_code == 200

    ok = client.post("/api/auth/password", headers=current,
                     json={"current_password": PASSWORD, "new_password": "a new good password"})
    assert ok.status_code == 200
    assert client.get("/api/auth/me", headers=current).status_code == 200
    assert client.get("/api/auth/me", headers=other).status_code == 401
    assert client.post("/api/auth/login", json={"username": "alice", "password": "a new good password"}).status_code == 200


def test_change_password_rejects_weak_new_passwords(env):
    client, service, _ = env
    headers = sign_in(client, service, "alice", "viewer")
    weak = client.post("/api/auth/password", headers=headers,
                       json={"current_password": PASSWORD, "new_password": "short"})
    assert weak.status_code == 422


# --- /api/users (admin) ---------------------------------------------------------------------------

@pytest.fixture
def admin(env):
    client, service, clock = env
    return client, service, sign_in(client, service, "boss", "admin")


def test_admin_lists_users_without_any_secret_material(admin):
    client, service, headers = admin
    service.create_user("alice", PASSWORD, "viewer", "Alice")
    body = client.get("/api/users", headers=headers).json()
    assert [u["username"] for u in body["users"]] == ["alice", "boss"]
    assert body["roles"] == ["viewer", "operator", "admin"]
    dump = json.dumps(body)
    assert "hash" not in dump and "scrypt" not in dump and PASSWORD not in dump


def test_admin_creates_users_with_201(admin):
    client, service, headers = admin
    response = client.post("/api/users", headers=headers, json={
        "username": "Carol", "password": PASSWORD, "role": "operator", "display_name": "Carol C."})
    assert response.status_code == 201
    assert response.json()["username"] == "carol" and response.json()["role"] == "operator"
    assert client.post("/api/auth/login", json={"username": "carol", "password": PASSWORD}).status_code == 200


@pytest.mark.parametrize("payload,status", [
    ({"username": "boss", "password": PASSWORD, "role": "viewer"}, 409),
    ({"username": "carol", "password": PASSWORD, "role": "root"}, 422),
    ({"username": "carol", "password": "short", "role": "viewer"}, 422),
    ({"username": "Bad Name", "password": PASSWORD, "role": "viewer"}, 422),
    ({"username": "carol", "password": PASSWORD}, 422),
])
def test_create_user_rejects_bad_input(admin, payload, status):
    client, _, headers = admin
    assert client.post("/api/users", headers=headers, json=payload).status_code == status


def test_admin_updates_role_status_name_and_password(admin):
    client, service, headers = admin
    user = service.create_user("alice", PASSWORD, "viewer")
    token = client.post("/api/auth/login", json={"username": "alice", "password": PASSWORD}).json()["token"]

    promoted = client.patch(f"/api/users/{user.id}", headers=headers, json={"role": "operator", "display_name": "Al"})
    assert promoted.status_code == 200 and promoted.json()["role"] == "operator"
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401  # ended

    assert client.patch(f"/api/users/{user.id}", headers=headers, json={"enabled": False}).json()["enabled"] is False
    assert client.post("/api/auth/login", json={"username": "alice", "password": PASSWORD}).status_code == 401
    client.patch(f"/api/users/{user.id}", headers=headers, json={"enabled": True, "password": "reset password 1"})
    assert client.post("/api/auth/login", json={"username": "alice", "password": "reset password 1"}).status_code == 200


def test_admin_deletes_a_user(admin):
    client, service, headers = admin
    user = service.create_user("alice", PASSWORD, "viewer")
    assert client.delete(f"/api/users/{user.id}", headers=headers).json() == {"status": "deleted"}
    assert [u["username"] for u in client.get("/api/users", headers=headers).json()["users"]] == ["boss"]


def test_unknown_user_ids_are_404(admin):
    client, _, headers = admin
    assert client.patch("/api/users/nope", headers=headers, json={"role": "viewer"}).status_code == 404
    assert client.delete("/api/users/nope", headers=headers).status_code == 404


def test_admins_cannot_change_or_delete_themselves(admin):
    client, service, headers = admin
    me = service.store.get_credentials("boss")["id"]
    assert client.patch(f"/api/users/{me}", headers=headers, json={"role": "viewer"}).status_code == 409
    assert client.patch(f"/api/users/{me}", headers=headers, json={"enabled": False}).status_code == 409
    assert client.delete(f"/api/users/{me}", headers=headers).status_code == 409
    # ...but may still change their own name/password
    assert client.patch(f"/api/users/{me}", headers=headers, json={"display_name": "Boss"}).status_code == 200


def test_the_last_admin_is_protected_from_other_admins_only_when_it_would_leave_none(admin):
    client, service, headers = admin
    second = service.create_user("second", PASSWORD, "admin")
    # boss demotes second: fine, boss remains
    assert client.patch(f"/api/users/{second.id}", headers=headers, json={"role": "viewer"}).status_code == 200


@pytest.mark.parametrize("role", ["viewer", "operator"])
def test_non_admins_get_403_on_every_user_route(env, role):
    client, service, _ = env
    headers = sign_in(client, service, f"{role}-user", role)
    target = service.create_user("target", PASSWORD, "viewer").id
    assert client.get("/api/users", headers=headers).status_code == 403
    assert client.post("/api/users", headers=headers,
                       json={"username": "x-user", "password": PASSWORD, "role": "viewer"}).status_code == 403
    assert client.patch(f"/api/users/{target}", headers=headers, json={"role": "admin"}).status_code == 403
    assert client.delete(f"/api/users/{target}", headers=headers).status_code == 403
    assert service.get_user(target).role == "viewer"


def test_anonymous_callers_get_401_on_every_user_route(env):
    client, _, _ = env
    assert client.get("/api/users").status_code == 401
    assert client.post("/api/users", json={"username": "x-user", "password": PASSWORD, "role": "viewer"}).status_code == 401
    assert client.patch("/api/users/x", json={"role": "admin"}).status_code == 401
    assert client.delete("/api/users/x").status_code == 401


def test_administration_is_audited_with_the_acting_admin(admin):
    client, service, headers = admin
    client.post("/api/users", headers=headers, json={"username": "carol", "password": PASSWORD, "role": "viewer"})
    events = [e for e in service.audit_store.recent(50) if e.action == "user_created"]
    assert any(e.metadata.get("username") == "carol" and e.metadata.get("by") == "boss" for e in events)
    assert PASSWORD not in " ".join(str(e) for e in service.audit_store.recent(50))


def test_user_routes_are_open_when_auth_is_disabled(tmp_path):
    client, _, _ = make(tmp_path, enabled=False)
    created = client.post("/api/users", json={"username": "carol", "password": PASSWORD, "role": "viewer"})
    assert created.status_code == 201
    assert client.get("/api/users").status_code == 200


# --- fail closed -------------------------------------------------------------------------------------------

def test_an_unavailable_auth_database_yields_503_not_access(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    with pytest.warns(RuntimeWarning):
        store = AuthStore(db_path=blocker / "sub" / "auth.sqlite3")
    service = AuthService(store, audit_store=AuditStore(db_path=tmp_path / "a.sqlite3"), scrypt_n=16)
    app = FastAPI()
    app.include_router(build_auth_router(service))
    client = TestClient(app)

    assert client.post("/api/auth/login", json={"username": "a", "password": "b"}).status_code == 503
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer anything"}).status_code == 503
    assert client.get("/api/users", headers={"Authorization": "Bearer anything"}).status_code == 503
    assert client.get("/api/auth/status").json()["users_configured"] is False
