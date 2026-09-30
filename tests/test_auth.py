"""
Tests for backend/auth.py: password hashing, users, sessions, lockout,
revocation, last-admin protection, fail-closed behaviour, first-run bootstrap,
audit hygiene and the CLI. Uses a cheap scrypt cost and an injectable clock.
"""
import hashlib
import sqlite3

import pytest

from backend import auth as auth_module
from backend.audit import AuditStore
from backend.auth import (
    AccountLocked, AuthService, AuthStore, DuplicateUser, InvalidCredentials, LastAdminError,
    ServiceUnavailable, UserNotFound, ValidationError, hash_password, verify_password,
)

PASSWORD = "correct horse battery"


class Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


def make_service(tmp_path, *, clock=None, **kwargs) -> AuthService:
    return AuthService(
        AuthStore(db_path=tmp_path / "auth.sqlite3"),
        audit_store=AuditStore(db_path=tmp_path / "audit.sqlite3"),
        scrypt_n=16, clock=clock or Clock(), **kwargs,
    )


# --- password hashing ---------------------------------------------------------

def test_hash_roundtrip_and_wrong_password():
    stored = hash_password(PASSWORD, n=16)
    assert stored.startswith("scrypt$16$")
    assert verify_password(PASSWORD, stored) is True
    assert verify_password("wrong password", stored) is False


def test_hashes_are_salted():
    assert hash_password(PASSWORD, n=16) != hash_password(PASSWORD, n=16)


@pytest.mark.parametrize("stored", ["", "garbage", "bcrypt$1$2$3$aa$bb", "scrypt$x$8$1$zz$zz", "scrypt$16$8$1$00$"])
def test_malformed_stored_hashes_never_verify(stored):
    assert verify_password(PASSWORD, stored) is False


# --- validation -----------------------------------------------------------------

@pytest.mark.parametrize("bad", ["", "ab", "Has Space", "-leading", "x" * 40, "semi;colon"])
def test_invalid_usernames_rejected(tmp_path, bad):
    with pytest.raises(ValidationError):
        make_service(tmp_path).create_user(bad, PASSWORD, "viewer")


@pytest.mark.parametrize("bad", ["", "short", "x" * 129])
def test_invalid_passwords_rejected(tmp_path, bad):
    with pytest.raises(ValidationError):
        make_service(tmp_path).create_user("alice", bad, "viewer")


def test_password_may_not_equal_username(tmp_path):
    with pytest.raises(ValidationError):
        make_service(tmp_path).create_user("alicealice", "AliceAlice", "viewer")


def test_invalid_role_rejected(tmp_path):
    with pytest.raises(ValidationError):
        make_service(tmp_path).create_user("alice", PASSWORD, "superuser")


# --- users --------------------------------------------------------------------------

def test_create_user_normalises_username_and_hides_the_hash(tmp_path):
    user = make_service(tmp_path).create_user("  Alice  ", PASSWORD, "operator", "Alice A.")
    assert user.username == "alice" and user.role == "operator" and user.enabled
    public = user.to_public()
    assert "password_hash" not in public and "password" not in str(public)


def test_duplicate_usernames_are_rejected_case_insensitively(tmp_path):
    service = make_service(tmp_path)
    service.create_user("alice", PASSWORD, "viewer")
    with pytest.raises(DuplicateUser):
        service.create_user("ALICE", PASSWORD, "admin")


def test_users_survive_a_new_store_instance(tmp_path):
    make_service(tmp_path).create_user("alice", PASSWORD, "viewer")
    reopened = make_service(tmp_path)
    assert [u.username for u in reopened.list_users()] == ["alice"]
    token, _, _ = reopened.login("alice", PASSWORD)
    assert reopened.authenticate(token).username == "alice"


def test_list_users_is_sorted_and_get_user_404s(tmp_path):
    service = make_service(tmp_path)
    service.create_user("zed", PASSWORD, "viewer")
    service.create_user("amy", PASSWORD, "viewer")
    assert [u.username for u in service.list_users()] == ["amy", "zed"]
    with pytest.raises(UserNotFound):
        service.get_user("nope")


# --- login / sessions ---------------------------------------------------------------------

def test_login_returns_token_expiry_and_user_and_records_last_login(tmp_path):
    service = make_service(tmp_path)
    service.create_user("alice", PASSWORD, "operator")
    token, expires_at, user = service.login("Alice", PASSWORD)
    assert len(token) >= 32 and expires_at and user.username == "alice"
    assert service.get_user(user.id).last_login_at is not None


def test_only_a_hash_of_the_token_is_stored(tmp_path):
    service = make_service(tmp_path)
    service.create_user("alice", PASSWORD, "viewer")
    token, _, _ = service.login("alice", PASSWORD)
    rows = sqlite3.connect(tmp_path / "auth.sqlite3").execute("SELECT token_hash FROM sessions").fetchall()
    assert rows == [(hashlib.sha256(token.encode()).hexdigest(),)]
    dump = " ".join(sqlite3.connect(tmp_path / "auth.sqlite3").iterdump())
    assert token not in dump and PASSWORD not in dump


def test_bad_password_and_unknown_user_get_the_same_error(tmp_path):
    service = make_service(tmp_path)
    service.create_user("alice", PASSWORD, "viewer")
    with pytest.raises(InvalidCredentials) as bad:
        service.login("alice", "wrong password")
    with pytest.raises(InvalidCredentials) as unknown:
        service.login("nobody", "wrong password")
    assert bad.value.message == unknown.value.message == "Invalid username or password."


def test_disabled_users_cannot_sign_in(tmp_path):
    service = make_service(tmp_path)
    admin = service.create_user("boss", PASSWORD, "admin")
    user = service.create_user("alice", PASSWORD, "viewer")
    service.update_user(user.id, enabled=False)
    with pytest.raises(InvalidCredentials):
        service.login("alice", PASSWORD)
    assert admin.enabled


def test_authenticate_rejects_missing_garbage_and_expired_tokens(tmp_path):
    clock = Clock()
    service = make_service(tmp_path, clock=clock, session_hours=1)
    service.create_user("alice", PASSWORD, "viewer")
    token, _, _ = service.login("alice", PASSWORD)

    assert service.authenticate(token).username == "alice"
    assert service.authenticate(None) is None and service.authenticate("") is None
    assert service.authenticate("not-a-real-token") is None
    clock.now += 3601
    assert service.authenticate(token) is None


def test_logout_revokes_only_that_session(tmp_path):
    service = make_service(tmp_path)
    user = service.create_user("alice", PASSWORD, "viewer")
    first, _, _ = service.login("alice", PASSWORD)
    second, _, _ = service.login("alice", PASSWORD)
    service.logout(first, user)
    assert service.authenticate(first) is None
    assert service.authenticate(second) is not None


# --- lockout -------------------------------------------------------------------------------------

def test_repeated_failures_lock_the_account_even_for_the_right_password(tmp_path):
    clock = Clock()
    service = make_service(tmp_path, clock=clock, max_failed_attempts=3, lockout_minutes=5)
    service.create_user("alice", PASSWORD, "viewer")
    for _ in range(3):
        with pytest.raises(InvalidCredentials):
            service.login("alice", "wrong password")
    with pytest.raises(AccountLocked):
        service.login("alice", PASSWORD)

    clock.now += 301
    token, _, _ = service.login("alice", PASSWORD)
    assert service.authenticate(token) is not None


def test_a_successful_login_resets_the_failure_count(tmp_path):
    service = make_service(tmp_path, max_failed_attempts=3)
    service.create_user("alice", PASSWORD, "viewer")
    for _ in range(2):
        with pytest.raises(InvalidCredentials):
            service.login("alice", "wrong password")
    service.login("alice", PASSWORD)
    for _ in range(2):  # would have locked at 3 cumulative failures
        with pytest.raises(InvalidCredentials):
            service.login("alice", "wrong password")
    service.login("alice", PASSWORD)


# --- revocation -------------------------------------------------------------------------------------

def _session(service, name="alice", role="operator"):
    user = service.create_user(name, PASSWORD, role)
    token, _, _ = service.login(name, PASSWORD)
    return user, token


def test_role_change_ends_existing_sessions(tmp_path):
    service = make_service(tmp_path)
    service.create_user("boss", PASSWORD, "admin")
    user, token = _session(service)
    service.update_user(user.id, role="viewer")
    assert service.authenticate(token) is None
    assert service.get_user(user.id).role == "viewer"


def test_disabling_or_resetting_password_ends_sessions(tmp_path):
    service = make_service(tmp_path)
    service.create_user("boss", PASSWORD, "admin")
    user, token = _session(service)
    service.update_user(user.id, password="a brand new password")
    assert service.authenticate(token) is None
    token2, _, _ = service.login("alice", "a brand new password")
    service.update_user(user.id, enabled=False)
    assert service.authenticate(token2) is None


def test_display_name_change_keeps_sessions(tmp_path):
    service = make_service(tmp_path)
    user, token = _session(service)
    service.update_user(user.id, display_name="Alice A.")
    assert service.authenticate(token) is not None


def test_deleting_a_user_removes_their_sessions(tmp_path):
    service = make_service(tmp_path)
    service.create_user("boss", PASSWORD, "admin")
    user, token = _session(service)
    service.delete_user(user.id)
    assert service.authenticate(token) is None
    assert service.store.count_sessions() == 0


def test_admin_password_reset_clears_a_lockout(tmp_path):
    service = make_service(tmp_path, max_failed_attempts=2)
    service.create_user("boss", PASSWORD, "admin")
    user = service.create_user("alice", PASSWORD, "viewer")
    for _ in range(2):
        with pytest.raises(InvalidCredentials):
            service.login("alice", "wrong password")
    with pytest.raises(AccountLocked):
        service.login("alice", PASSWORD)
    service.update_user(user.id, password="fresh password 1")
    service.login("alice", "fresh password 1")


# --- change password ------------------------------------------------------------------------------------

def test_change_password_requires_the_current_one_and_keeps_only_the_current_session(tmp_path):
    service = make_service(tmp_path)
    user, current = _session(service)
    other, _, _ = service.login("alice", PASSWORD)

    with pytest.raises(InvalidCredentials):
        service.change_password(user, "wrong current", "another good password")
    service.change_password(user, PASSWORD, "another good password", keep_token=current)

    assert service.authenticate(current) is not None
    assert service.authenticate(other) is None
    service.login("alice", "another good password")
    with pytest.raises(InvalidCredentials):
        service.login("alice", PASSWORD)


def test_change_password_enforces_the_policy(tmp_path):
    service = make_service(tmp_path)
    user, _ = _session(service)
    with pytest.raises(ValidationError):
        service.change_password(user, PASSWORD, "short")


# --- last administrator ------------------------------------------------------------------------------------

def test_the_last_enabled_admin_cannot_be_removed_demoted_or_disabled(tmp_path):
    service = make_service(tmp_path)
    admin = service.create_user("boss", PASSWORD, "admin")
    for change in ({"role": "operator"}, {"enabled": False}):
        with pytest.raises(LastAdminError):
            service.update_user(admin.id, **change)
    with pytest.raises(LastAdminError):
        service.delete_user(admin.id)


def test_admins_can_be_changed_when_another_remains(tmp_path):
    service = make_service(tmp_path)
    first = service.create_user("boss", PASSWORD, "admin")
    service.create_user("second", PASSWORD, "admin")
    service.update_user(first.id, role="viewer")
    assert service.get_user(first.id).role == "viewer"


# --- fail closed -----------------------------------------------------------------------------------------------

def test_unavailable_store_fails_closed(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    with pytest.warns(RuntimeWarning):
        service = AuthService(AuthStore(db_path=blocker / "sub" / "auth.sqlite3"),
                              audit_store=AuditStore(db_path=tmp_path / "a.sqlite3"), scrypt_n=16)
    assert service.users_configured() is False
    for call in (lambda: service.login("alice", PASSWORD), lambda: service.authenticate("token"),
                 lambda: service.create_user("alice", PASSWORD, "viewer"), service.list_users):
        with pytest.raises(ServiceUnavailable):
            call()


# --- bootstrap ---------------------------------------------------------------------------------------------------

def test_bootstrap_creates_the_first_admin_from_the_environment(tmp_path):
    service = make_service(tmp_path)
    user = service.bootstrap_from_env({"INTELLIGENZIT_ADMIN_USERNAME": "root",
                                       "INTELLIGENZIT_ADMIN_PASSWORD": PASSWORD})
    assert user.role == "admin" and user.username == "root"
    service.login("root", PASSWORD)


def test_bootstrap_is_a_noop_when_users_exist_or_env_is_missing(tmp_path):
    service = make_service(tmp_path)
    assert service.bootstrap_from_env({}) is None
    assert service.bootstrap_from_env({"INTELLIGENZIT_ADMIN_USERNAME": "root"}) is None
    service.create_user("alice", PASSWORD, "viewer")
    assert service.bootstrap_from_env({"INTELLIGENZIT_ADMIN_USERNAME": "root",
                                       "INTELLIGENZIT_ADMIN_PASSWORD": PASSWORD}) is None
    assert [u.username for u in service.list_users()] == ["alice"]


def test_bootstrap_with_an_invalid_password_warns_without_echoing_it(tmp_path):
    service = make_service(tmp_path)
    with pytest.warns(RuntimeWarning) as caught:
        result = service.bootstrap_from_env({"INTELLIGENZIT_ADMIN_USERNAME": "root",
                                             "INTELLIGENZIT_ADMIN_PASSWORD": "short"})
    assert result is None
    assert "short" not in " ".join(str(w.message) for w in caught)


# --- audit hygiene ---------------------------------------------------------------------------------------------------

def test_authentication_events_are_audited_without_secrets(tmp_path):
    service = make_service(tmp_path)
    user = service.create_user("alice", PASSWORD, "operator")
    token, _, _ = service.login("alice", PASSWORD)
    with pytest.raises(InvalidCredentials):
        service.login("alice", "wrong password guess")
    service.logout(token, user)

    events = service.audit_store.recent(50)
    assert {e.action for e in events} >= {"user_created", "login", "login_failed", "logout"}
    dump = " ".join(str(e) for e in events)
    assert PASSWORD not in dump and "wrong password guess" not in dump and token not in dump
    failed = next(e for e in events if e.action == "login_failed")
    assert failed.metadata["reason"] == "bad_password" and failed.metadata["username"] == "alice"


def test_lockout_is_audited(tmp_path):
    service = make_service(tmp_path, max_failed_attempts=2)
    service.create_user("alice", PASSWORD, "viewer")
    for _ in range(2):
        with pytest.raises(InvalidCredentials):
            service.login("alice", "wrong password")
    assert any(e.action == "account_locked" for e in service.audit_store.recent(50))


def test_a_broken_audit_backend_never_blocks_authentication(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    with pytest.warns(RuntimeWarning):
        broken = AuditStore(db_path=blocker / "sub" / "audit.sqlite3")
    service = AuthService(AuthStore(db_path=tmp_path / "auth.sqlite3"), audit_store=broken, scrypt_n=16)
    service.create_user("alice", PASSWORD, "viewer")
    token, _, _ = service.login("alice", PASSWORD)
    assert service.authenticate(token) is not None


# --- configuration ---------------------------------------------------------------------------------------------------------

def test_default_service_honours_the_explicit_opt_out(monkeypatch):
    monkeypatch.setenv("INTELLIGENZIT_AUTH_DISABLED", "1")
    with pytest.warns(RuntimeWarning, match="NOT enforced"):
        service = auth_module.build_default_service()
    assert service.enabled is False


def test_default_service_is_enabled_and_parses_session_hours(monkeypatch):
    monkeypatch.delenv("INTELLIGENZIT_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("INTELLIGENZIT_SESSION_HOURS", "2")
    assert auth_module.build_default_service().session_seconds == 7200
    monkeypatch.setenv("INTELLIGENZIT_SESSION_HOURS", "not-a-number")
    assert auth_module.build_default_service().session_seconds == 12 * 3600


def test_auth_lives_in_its_own_database():
    from backend import audit, memory, run_store
    paths = {auth_module.DEFAULT_DB_PATH, audit.DEFAULT_DB_PATH, memory.DEFAULT_DB_PATH,
             run_store.DEFAULT_DB_PATH}
    assert len(paths) == 4 and auth_module.DEFAULT_DB_PATH.name == "auth.sqlite3"


# --- CLI --------------------------------------------------------------------------------------------------------------------------

def test_cli_creates_lists_and_resets(tmp_path, monkeypatch, capsys):
    db = str(tmp_path / "cli.sqlite3")
    cli_audit = AuditStore(db_path=tmp_path / "cli_audit.sqlite3")
    monkeypatch.setenv("IZ_TEST_PW", PASSWORD)

    assert auth_module.main(["--db", db, "create-user", "--username", "alice", "--role", "admin",
                             "--display-name", "Alice", "--password-env", "IZ_TEST_PW"], audit_store=cli_audit) == 0
    assert "Created admin 'alice'" in capsys.readouterr().out

    assert auth_module.main(["--db", db, "list-users"], audit_store=cli_audit) == 0
    assert "alice\tadmin\tenabled\tAlice" in capsys.readouterr().out

    monkeypatch.setenv("IZ_TEST_PW", "a second good password")
    assert auth_module.main(["--db", db, "reset-password", "--username", "alice",
                             "--password-env", "IZ_TEST_PW"], audit_store=cli_audit) == 0
    service = AuthService(AuthStore(db_path=tmp_path / "cli.sqlite3"),
                          audit_store=AuditStore(db_path=tmp_path / "cli_audit.sqlite3"), scrypt_n=16)
    service.login("alice", "a second good password")


def test_cli_reports_errors_without_a_traceback(tmp_path, monkeypatch, capsys):
    db = str(tmp_path / "cli.sqlite3")
    cli_audit = AuditStore(db_path=tmp_path / "cli_audit.sqlite3")
    monkeypatch.setenv("IZ_TEST_PW", PASSWORD)
    args = ["--db", db, "create-user", "--username", "alice", "--role", "viewer", "--password-env", "IZ_TEST_PW"]
    assert auth_module.main(args, audit_store=cli_audit) == 0
    assert auth_module.main(args, audit_store=cli_audit) == 1  # duplicate
    assert "already exists" in capsys.readouterr().err
    assert auth_module.main(["--db", db, "reset-password", "--username", "ghost",
                             "--password-env", "IZ_TEST_PW"], audit_store=cli_audit) == 1
    with pytest.raises(SystemExit):
        auth_module.main(["--db", db, "create-user", "--username", "bob", "--role", "root",
                          "--password-env", "IZ_TEST_PW"], audit_store=cli_audit)
    with pytest.raises(SystemExit):
        monkeypatch.delenv("IZ_TEST_PW")
        auth_module.main(["--db", db, "create-user", "--username", "bob", "--role", "viewer",
                          "--password-env", "IZ_TEST_PW"], audit_store=cli_audit)
