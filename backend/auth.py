"""
Local authentication and role-based access control.

Users and sessions live in their own SQLite database (data/auth.sqlite3),
separate from memory, knowledge base, run history and the audit log. There is
no external identity provider: accounts are created by an administrator (via
the API, or the CLI / first-run environment seed below).

Roles (each includes everything the previous one can do):
    viewer   - read-only access to workflows, agents, memory, metrics, etc.
    operator - viewer + create/execute workflows
    admin    - operator + user management, governance view and audit log

Design notes:
- Passwords are hashed with scrypt (stdlib), a random per-user salt, and
  compared in constant time. Parameters are stored in each hash so they can
  be raised later without invalidating existing accounts.
- Sessions are opaque random bearer tokens. Only a SHA-256 hash of the token
  is stored, so a database leak does not yield usable tokens. Sessions expire,
  are revoked on logout, and are all revoked when a user's password, role or
  enabled state changes or the user is deleted.
- Repeated failed logins lock the account briefly. Login errors are uniform
  ("Invalid username or password.") whether or not the user exists, and an
  unknown username still costs a real hash verification to blunt timing probes.
- Authentication fails closed: if the auth database is unavailable, protected
  routes answer 503 rather than letting anyone through.
- Authentication and administration events are written to the audit log
  (never passwords or tokens). Only the explicit opt-out
  INTELLIGENZIT_AUTH_DISABLED=1 turns enforcement off, for local development.

CLI (run from the project root):
    python -m backend.auth create-user --username alice --role admin
    python -m backend.auth list-users
    python -m backend.auth reset-password --username alice
Passwords are prompted for (hidden), or read from an env var via --password-env.
"""
import argparse
import getpass
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import sys
import time
import uuid
import warnings
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from fastapi import HTTPException, Request

from .audit import AuditStore, default_audit_store

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "auth.sqlite3"

ROLES = ("viewer", "operator", "admin")
ROLE_RANK = {role: index + 1 for index, role in enumerate(ROLES)}

USERNAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]{2,31}$")
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128

_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_MAXMEM = 128 * 1024 * 1024

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'operator', 'viewer')),
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_login_at TEXT,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at REAL NOT NULL
);
"""

_USER_COLUMNS = ("display_name", "password_hash", "role", "enabled")


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------
class AuthError(Exception):
    """Base class. `status` is the HTTP status an API layer should use."""
    status = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class ValidationError(AuthError):
    status = 422


class InvalidCredentials(AuthError):
    status = 401


class AccountLocked(AuthError):
    status = 429


class DuplicateUser(AuthError):
    status = 409


class UserNotFound(AuthError):
    status = 404


class LastAdminError(AuthError):
    status = 409


class ServiceUnavailable(AuthError):
    status = 503


# ---------------------------------------------------------------------------
# password hashing
# ---------------------------------------------------------------------------
def hash_password(password: str, *, n: int = _SCRYPT_N) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=_SCRYPT_R, p=_SCRYPT_P,
                            maxmem=_SCRYPT_MAXMEM, dklen=32)
    return f"scrypt${n}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt_hex), n=int(n),
                                r=int(r), p=int(p), maxmem=_SCRYPT_MAXMEM, dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# model
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class User:
    id: str
    username: str
    display_name: str
    role: str
    enabled: bool
    created_at: str
    updated_at: str
    last_login_at: Optional[str]

    def to_public(self) -> dict:
        """Everything safe to return from the API (never a hash or token)."""
        return {
            "id": self.id, "username": self.username, "display_name": self.display_name,
            "role": self.role, "enabled": self.enabled, "created_at": self.created_at,
            "updated_at": self.updated_at, "last_login_at": self.last_login_at,
        }


def _row_to_user(row: sqlite3.Row) -> User:
    return User(
        id=row["id"], username=row["username"], display_name=row["display_name"], role=row["role"],
        enabled=bool(row["enabled"]), created_at=row["created_at"], updated_at=row["updated_at"],
        last_login_at=row["last_login_at"],
    )


# ---------------------------------------------------------------------------
# store
# ---------------------------------------------------------------------------
class AuthStore:
    """SQLite persistence for users and sessions. One short-lived connection
    per operation. If the database can't be opened, is_available is False and
    AuthService fails closed."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
        self._unavailable = False
        self._init_db()

    @property
    def is_available(self) -> bool:
        return not self._unavailable

    def _init_db(self) -> None:
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            with self._connection() as conn:
                conn.executescript(_SCHEMA)
        except (sqlite3.Error, OSError) as exc:
            self._unavailable = True
            warnings.warn(
                f"AuthStore: database unavailable at {self.db_path} ({exc}); "
                "protected routes will refuse requests until it is fixed.",
                RuntimeWarning,
            )

    @contextmanager
    def _connection(self):
        conn = sqlite3.connect(str(self.db_path), timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # -- users --------------------------------------------------------------
    def add_user(self, *, username: str, display_name: str, password_hash: str, role: str) -> User:
        now = datetime.now(timezone.utc).isoformat()
        user_id = str(uuid.uuid4())
        try:
            with self._connection() as conn:
                conn.execute(
                    "INSERT INTO users (id, username, display_name, password_hash, role, enabled, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                    (user_id, username, display_name, password_hash, role, now, now))
        except sqlite3.IntegrityError:
            raise DuplicateUser(f"A user named '{username}' already exists.") from None
        return self.get_user(user_id)

    def get_user(self, user_id: str) -> Optional[User]:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return _row_to_user(row) if row else None

    def get_credentials(self, username: str) -> Optional[sqlite3.Row]:
        """Raw row (including hash and lockout state) for login checks."""
        with self._connection() as conn:
            return conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()

    def get_password_hash(self, user_id: str) -> Optional[str]:
        with self._connection() as conn:
            row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
        return row["password_hash"] if row else None

    def list_users(self) -> list:
        with self._connection() as conn:
            rows = conn.execute("SELECT * FROM users ORDER BY username").fetchall()
        return [_row_to_user(r) for r in rows]

    def count_users(self) -> int:
        with self._connection() as conn:
            return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def count_enabled_admins(self) -> int:
        with self._connection() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM users WHERE role = 'admin' AND enabled = 1").fetchone()[0]

    def update_user(self, user_id: str, **fields) -> None:
        columns = [c for c in fields if c in _USER_COLUMNS]
        assignments = ", ".join(f"{c} = ?" for c in columns)
        values = [int(fields[c]) if c == "enabled" else fields[c] for c in columns]
        with self._connection() as conn:
            conn.execute(f"UPDATE users SET {assignments}, updated_at = ? WHERE id = ?",
                         (*values, datetime.now(timezone.utc).isoformat(), user_id))

    def delete_user(self, user_id: str) -> None:
        with self._connection() as conn:
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))

    def record_failure(self, user_id: str, attempts: int, locked_until: float) -> None:
        with self._connection() as conn:
            conn.execute("UPDATE users SET failed_attempts = ?, locked_until = ? WHERE id = ?",
                         (attempts, locked_until, user_id))

    def record_login(self, user_id: str) -> None:
        with self._connection() as conn:
            conn.execute(
                "UPDATE users SET failed_attempts = 0, locked_until = 0, last_login_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), user_id))

    # -- sessions -----------------------------------------------------------
    def add_session(self, token_hash: str, user_id: str, expires_at: float) -> None:
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (token_hash, user_id, datetime.now(timezone.utc).isoformat(), expires_at))

    def get_session_user(self, token_hash: str, now: float) -> Optional[User]:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id "
                "WHERE s.token_hash = ? AND s.expires_at > ? AND u.enabled = 1",
                (token_hash, now)).fetchone()
        return _row_to_user(row) if row else None

    def delete_session(self, token_hash: str) -> None:
        with self._connection() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

    def delete_user_sessions(self, user_id: str, *, except_hash: Optional[str] = None) -> None:
        with self._connection() as conn:
            if except_hash:
                conn.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash != ?",
                             (user_id, except_hash))
            else:
                conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))

    def purge_expired(self, now: float) -> None:
        with self._connection() as conn:
            conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))

    def count_sessions(self) -> int:
        with self._connection() as conn:
            return conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]


# ---------------------------------------------------------------------------
# service
# ---------------------------------------------------------------------------
def _validate_username(username: str) -> str:
    normalized = (username or "").strip().lower()
    if not USERNAME_PATTERN.match(normalized):
        raise ValidationError(
            "Username must be 3-32 characters: lowercase letters, digits, '.', '_' or '-', "
            "starting with a letter or digit.")
    return normalized


def _validate_password(password: str, username: str = "") -> None:
    if not isinstance(password, str) or not (MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH):
        raise ValidationError(
            f"Password must be between {MIN_PASSWORD_LENGTH} and {MAX_PASSWORD_LENGTH} characters.")
    if username and password.lower() == username.lower():
        raise ValidationError("Password must not be the same as the username.")


def _validate_role(role: str) -> str:
    if role not in ROLE_RANK:
        raise ValidationError(f"Role must be one of: {', '.join(ROLES)}.")
    return role


def _extract_token(request: Request) -> Optional[str]:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        return value.strip()
    # The frontend already stores one "API key" value and sends it as X-API-Key.
    return (request.headers.get("x-api-key") or "").strip() or None


class AuthService:
    def __init__(
        self,
        store: AuthStore,
        *,
        audit_store: Optional[AuditStore] = None,
        enabled: bool = True,
        session_hours: float = 12.0,
        max_failed_attempts: int = 5,
        lockout_minutes: float = 5.0,
        scrypt_n: int = _SCRYPT_N,
        clock: Callable[[], float] = time.time,
    ):
        self.store = store
        self.audit_store = audit_store if audit_store is not None else default_audit_store
        self.enabled = enabled
        self.session_seconds = session_hours * 3600
        self.max_failed_attempts = max_failed_attempts
        self.lockout_seconds = lockout_minutes * 60
        self.scrypt_n = scrypt_n
        self._clock = clock
        self._dummy_hash: Optional[str] = None

    # -- helpers ------------------------------------------------------------
    def _require_store(self) -> None:
        if not self.store.is_available:
            raise ServiceUnavailable("The authentication service is unavailable.")

    def _audit(self, action: str, status: str, **metadata) -> None:
        try:
            self.audit_store.append(actor="auth", action=action, status=status, metadata=metadata)
        except Exception as exc:  # audit must never break authentication
            warnings.warn(f"AuthService: audit logging failed ({exc})", RuntimeWarning)

    def _burn_hash_time(self, password: str) -> None:
        if self._dummy_hash is None:
            self._dummy_hash = hash_password("intelligenzit-dummy", n=self.scrypt_n)
        verify_password(password, self._dummy_hash)

    # -- status -------------------------------------------------------------
    def users_configured(self) -> bool:
        return self.store.is_available and self.store.count_users() > 0

    # -- user administration ------------------------------------------------
    def create_user(self, username: str, password: str, role: str, display_name: str = "",
                    *, actor: str = "system") -> User:
        self._require_store()
        username = _validate_username(username)
        _validate_password(password, username)
        _validate_role(role)
        user = self.store.add_user(
            username=username, display_name=(display_name or "").strip()[:80],
            password_hash=hash_password(password, n=self.scrypt_n), role=role)
        self._audit("user_created", "success", username=user.username, role=user.role, by=actor)
        return user

    def get_user(self, user_id: str) -> User:
        self._require_store()
        user = self.store.get_user(user_id)
        if user is None:
            raise UserNotFound("No such user.")
        return user

    def list_users(self) -> list:
        self._require_store()
        return self.store.list_users()

    def _guard_last_admin(self, user: User, *, still_admin: bool, still_enabled: bool) -> None:
        if user.role == "admin" and user.enabled and not (still_admin and still_enabled):
            if self.store.count_enabled_admins() <= 1:
                raise LastAdminError("At least one enabled administrator must remain.")

    def update_user(self, user_id: str, *, role: Optional[str] = None, enabled: Optional[bool] = None,
                    display_name: Optional[str] = None, password: Optional[str] = None,
                    actor: str = "system") -> User:
        self._require_store()
        user = self.get_user(user_id)
        fields = {}
        if role is not None:
            fields["role"] = _validate_role(role)
        if enabled is not None:
            fields["enabled"] = bool(enabled)
        if display_name is not None:
            fields["display_name"] = display_name.strip()[:80]
        if password is not None:
            _validate_password(password, user.username)
            fields["password_hash"] = hash_password(password, n=self.scrypt_n)
        self._guard_last_admin(
            user, still_admin=fields.get("role", user.role) == "admin",
            still_enabled=fields.get("enabled", user.enabled))

        if fields:
            self.store.update_user(user_id, **fields)
        # Anything that changes what a session is allowed to do ends existing sessions.
        if {"role", "enabled", "password_hash"} & set(fields):
            self.store.delete_user_sessions(user_id)
        if "password_hash" in fields:  # an admin reset also clears any lockout
            self.store.record_failure(user_id, 0, 0)
        changed = sorted(("password" if k == "password_hash" else k) for k in fields)
        self._audit("user_updated", "success", username=user.username, changed=changed, by=actor)
        return self.get_user(user_id)

    def delete_user(self, user_id: str, *, actor: str = "system") -> None:
        self._require_store()
        user = self.get_user(user_id)
        self._guard_last_admin(user, still_admin=False, still_enabled=False)
        self.store.delete_user(user_id)  # sessions cascade
        self._audit("user_deleted", "success", username=user.username, by=actor)

    def change_password(self, user: User, current_password: str, new_password: str,
                        *, keep_token: Optional[str] = None) -> None:
        self._require_store()
        stored = self.store.get_password_hash(user.id)
        if stored is None or not verify_password(current_password or "", stored):
            self._audit("password_change_failed", "failed", username=user.username)
            raise InvalidCredentials("The current password is incorrect.")
        _validate_password(new_password, user.username)
        self.store.update_user(user.id, password_hash=hash_password(new_password, n=self.scrypt_n))
        self.store.delete_user_sessions(
            user.id, except_hash=_hash_token(keep_token) if keep_token else None)
        self._audit("password_changed", "success", username=user.username)

    # -- sessions -------------------------------------------------------------
    def login(self, username: str, password: str) -> tuple:
        """Returns (token, expires_at_iso, User). Raises InvalidCredentials /
        AccountLocked / ServiceUnavailable."""
        self._require_store()
        name = (username or "").strip().lower()
        row = self.store.get_credentials(name)
        now = self._clock()
        safe_name = name[:64]

        if row is None or not row["enabled"]:
            self._burn_hash_time(password or "")
            self._audit("login_failed", "failed", username=safe_name,
                        reason="unknown_user" if row is None else "disabled")
            raise InvalidCredentials("Invalid username or password.")

        if row["locked_until"] > now:
            self._audit("login_failed", "failed", username=safe_name, reason="locked")
            raise AccountLocked("Too many failed sign-in attempts. Try again in a few minutes.")

        if not verify_password(password or "", row["password_hash"]):
            attempts = row["failed_attempts"] + 1
            locked_until = now + self.lockout_seconds if attempts >= self.max_failed_attempts else 0
            self.store.record_failure(row["id"], 0 if locked_until else attempts, locked_until)
            self._audit("login_failed", "failed", username=safe_name, reason="bad_password")
            if locked_until:
                self._audit("account_locked", "failed", username=safe_name)
            raise InvalidCredentials("Invalid username or password.")

        self.store.purge_expired(now)
        token = secrets.token_urlsafe(32)
        expires_at = now + self.session_seconds
        self.store.add_session(_hash_token(token), row["id"], expires_at)
        self.store.record_login(row["id"])
        user = self.store.get_user(row["id"])
        self._audit("login", "success", username=user.username, role=user.role)
        return token, _iso(expires_at), user

    def authenticate(self, token: Optional[str]) -> Optional[User]:
        """The user a valid, unexpired session belongs to, else None.
        Raises ServiceUnavailable if the auth database is down (fail closed)."""
        self._require_store()
        if not token:
            return None
        return self.store.get_session_user(_hash_token(token), self._clock())

    def logout(self, token: Optional[str], user: Optional[User] = None) -> None:
        if token and self.store.is_available:
            self.store.delete_session(_hash_token(token))
            if user:
                self._audit("logout", "success", username=user.username)

    # -- FastAPI integration ---------------------------------------------------
    def require(self, min_role: str = "viewer") -> Callable:
        """Build a FastAPI dependency enforcing authentication and a minimum
        role. Returns the User (or None when enforcement is disabled)."""
        rank = ROLE_RANK[_validate_role(min_role)]

        def dependency(request: Request) -> Optional[User]:
            if not self.enabled:
                return None
            token = _extract_token(request)
            try:
                user = self.authenticate(token)
            except ServiceUnavailable as exc:
                raise HTTPException(503, exc.message) from None
            if user is None:
                raise HTTPException(401, "Authentication required.",
                                    headers={"WWW-Authenticate": "Bearer"})
            if ROLE_RANK[user.role] < rank:
                self._audit("access_denied", "denied", username=user.username, role=user.role,
                            required_role=min_role, method=request.method, path=request.url.path[:120])
                raise HTTPException(403, "Your role does not permit this action.")
            request.state.user = user
            request.state.token = token
            return user

        return dependency

    # -- bootstrap ---------------------------------------------------------------
    def bootstrap_from_env(self, environ=None) -> Optional[User]:
        """First-run convenience: if no users exist and INTELLIGENZIT_ADMIN_USERNAME /
        INTELLIGENZIT_ADMIN_PASSWORD are set, create that administrator."""
        environ = os.environ if environ is None else environ
        username = environ.get("INTELLIGENZIT_ADMIN_USERNAME")
        password = environ.get("INTELLIGENZIT_ADMIN_PASSWORD")
        if not (username and password) or not self.store.is_available:
            return None
        if self.store.count_users() > 0:
            return None
        try:
            return self.create_user(username, password, "admin", "Administrator", actor="bootstrap")
        except AuthError as exc:  # never echo the password
            warnings.warn(f"AuthService: could not create the bootstrap administrator: {exc.message}",
                          RuntimeWarning)
            return None


# ---------------------------------------------------------------------------
# default service
# ---------------------------------------------------------------------------
def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def build_default_service() -> AuthService:
    enabled = not _env_flag("INTELLIGENZIT_AUTH_DISABLED")
    if not enabled:
        warnings.warn("INTELLIGENZIT_AUTH_DISABLED is set: authentication is NOT enforced. "
                      "Use only for local development.", RuntimeWarning)
    try:
        hours = float(os.environ.get("INTELLIGENZIT_SESSION_HOURS", "12"))
    except ValueError:
        hours = 12.0
    return AuthService(AuthStore(), enabled=enabled, session_hours=hours)


default_auth_service = build_default_service()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _read_password(args) -> str:
    if args.password_env:
        value = os.environ.get(args.password_env)
        if not value:
            raise SystemExit(f"Environment variable {args.password_env} is not set.")
        return value
    first = getpass.getpass("Password: ")
    if first != getpass.getpass("Repeat password: "):
        raise SystemExit("Passwords do not match.")
    return first


def main(argv=None, *, audit_store: Optional[AuditStore] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m backend.auth", description="Manage Intelligenzit users.")
    parser.add_argument("--db", help="Path to the auth database (default: data/auth.sqlite3).")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create-user", help="Create a user.")
    create.add_argument("--username", required=True)
    create.add_argument("--role", required=True, choices=ROLES)
    create.add_argument("--display-name", default="")
    create.add_argument("--password-env", help="Read the password from this environment variable.")

    sub.add_parser("list-users", help="List users.")

    reset = sub.add_parser("reset-password", help="Set a new password (also clears any lockout).")
    reset.add_argument("--username", required=True)
    reset.add_argument("--password-env")

    args = parser.parse_args(argv)
    service = AuthService(AuthStore(Path(args.db) if args.db else None), audit_store=audit_store)
    if not service.store.is_available:
        print("The auth database is unavailable.", file=sys.stderr)
        return 2

    try:
        if args.command == "create-user":
            user = service.create_user(args.username, _read_password(args), args.role,
                                       args.display_name, actor="cli")
            print(f"Created {user.role} '{user.username}'.")
        elif args.command == "list-users":
            for user in service.list_users():
                state = "enabled" if user.enabled else "disabled"
                print(f"{user.username}\t{user.role}\t{state}\t{user.display_name}")
        elif args.command == "reset-password":
            target = service.store.get_credentials(args.username.strip().lower())
            if target is None:
                print("No such user.", file=sys.stderr)
                return 1
            service.update_user(target["id"], password=_read_password(args), actor="cli")
            print(f"Password updated for '{target['username']}'.")
    except AuthError as exc:
        print(exc.message, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
