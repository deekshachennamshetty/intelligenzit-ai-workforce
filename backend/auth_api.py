"""
HTTP routes for authentication and user administration.

Public:            POST /api/auth/login, GET /api/auth/status
Any signed-in user: GET /api/auth/me, POST /api/auth/logout, POST /api/auth/password
Admin only:         GET/POST /api/users, PATCH/DELETE /api/users/{id}

Auth errors are translated to HTTP by their `status` (401/403/404/409/422/429/503).
Responses never contain a password hash or a session token other than the one
issued to the caller by /api/auth/login.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .auth import ROLES, AuthError, AuthService, User


class LoginBody(BaseModel):
    username: str = Field(..., max_length=64)
    password: str = Field(..., max_length=256)


class PasswordBody(BaseModel):
    current_password: str = Field(..., max_length=256)
    new_password: str = Field(..., max_length=256)


class CreateUserBody(BaseModel):
    username: str = Field(..., max_length=64)
    password: str = Field(..., max_length=256)
    role: str
    display_name: str = Field("", max_length=80)


class UpdateUserBody(BaseModel):
    role: Optional[str] = None
    enabled: Optional[bool] = None
    display_name: Optional[str] = Field(None, max_length=80)
    password: Optional[str] = Field(None, max_length=256)


def _http(exc: AuthError) -> HTTPException:
    return HTTPException(exc.status, exc.message)


def build_auth_router(service: AuthService) -> APIRouter:
    router = APIRouter()
    any_user = service.require("viewer")
    admin_only = service.require("admin")

    @router.get("/api/auth/status", tags=["auth"])
    def auth_status() -> dict:
        return {
            "auth_enabled": service.enabled,
            "users_configured": service.users_configured() if service.enabled else True,
            "roles": list(ROLES),
        }

    @router.post("/api/auth/login", tags=["auth"])
    def login(body: LoginBody) -> dict:
        if not service.enabled:
            raise HTTPException(409, "Authentication is disabled on this platform.")
        try:
            token, expires_at, user = service.login(body.username, body.password)
        except AuthError as exc:
            raise _http(exc) from None
        return {"token": token, "token_type": "bearer", "expires_at": expires_at,
                "user": user.to_public()}

    @router.get("/api/auth/me", tags=["auth"])
    def me(user: Optional[User] = Depends(any_user)) -> dict:
        if user is None:  # enforcement disabled: everyone is a local administrator
            return {"user": {"id": "local", "username": "local", "display_name": "Local (auth disabled)",
                             "role": "admin", "enabled": True, "created_at": None,
                             "updated_at": None, "last_login_at": None},
                    "auth_enabled": False}
        return {"user": user.to_public(), "auth_enabled": True}

    @router.post("/api/auth/logout", tags=["auth"])
    def logout(request: Request, user: Optional[User] = Depends(any_user)) -> dict:
        service.logout(getattr(request.state, "token", None), user)
        return {"status": "signed_out"}

    @router.post("/api/auth/password", tags=["auth"])
    def change_password(body: PasswordBody, request: Request,
                        user: Optional[User] = Depends(any_user)) -> dict:
        if user is None:
            raise HTTPException(409, "Authentication is disabled on this platform.")
        try:
            service.change_password(user, body.current_password, body.new_password,
                                    keep_token=getattr(request.state, "token", None))
        except AuthError as exc:
            # A wrong *current password* is a 422 here, not a session failure.
            raise HTTPException(422 if exc.status == 401 else exc.status, exc.message) from None
        return {"status": "password_changed"}

    # -- administration --------------------------------------------------------
    @router.get("/api/users", tags=["users"])
    def list_users(_: Optional[User] = Depends(admin_only)) -> dict:
        try:
            return {"users": [u.to_public() for u in service.list_users()], "roles": list(ROLES)}
        except AuthError as exc:
            raise _http(exc) from None

    @router.post("/api/users", tags=["users"], status_code=201)
    def create_user(body: CreateUserBody, admin: Optional[User] = Depends(admin_only)) -> dict:
        try:
            user = service.create_user(body.username, body.password, body.role, body.display_name,
                                       actor=admin.username if admin else "system")
        except AuthError as exc:
            raise _http(exc) from None
        return user.to_public()

    @router.patch("/api/users/{user_id}", tags=["users"])
    def update_user(user_id: str, body: UpdateUserBody,
                    admin: Optional[User] = Depends(admin_only)) -> dict:
        if admin is not None and admin.id == user_id and (
                body.role is not None or body.enabled is not None):
            raise HTTPException(409, "You cannot change your own role or enabled state.")
        try:
            user = service.update_user(
                user_id, role=body.role, enabled=body.enabled, display_name=body.display_name,
                password=body.password, actor=admin.username if admin else "system")
        except AuthError as exc:
            raise _http(exc) from None
        return user.to_public()

    @router.delete("/api/users/{user_id}", tags=["users"])
    def delete_user(user_id: str, admin: Optional[User] = Depends(admin_only)) -> dict:
        if admin is not None and admin.id == user_id:
            raise HTTPException(409, "You cannot delete your own account.")
        try:
            service.delete_user(user_id, actor=admin.username if admin else "system")
        except AuthError as exc:
            raise _http(exc) from None
        return {"status": "deleted"}

    return router
