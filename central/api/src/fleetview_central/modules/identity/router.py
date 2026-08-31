"""/api/v1/auth — autentikasi user dan device."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from fleetview_central.http.envelope import success
from fleetview_central.modules.identity.service import IdentityService
from fleetview_central.modules.ops.service import AuditService
from fleetview_central.platform.deps import CurrentUser, DbSession

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: Annotated[str, Field(min_length=1, max_length=64)]
    password: Annotated[str, Field(min_length=1)]


class DeviceLoginRequest(BaseModel):
    client_id: Annotated[str, Field(min_length=1, max_length=64)]
    secret: Annotated[str, Field(min_length=1)]


@router.post("/login", summary="Login operator")
async def login(body: LoginRequest, request: Request, db: DbSession) -> dict[str, Any]:
    tokens = request.app.state.tokens
    token = await IdentityService(db, tokens).authenticate_user(body.username, body.password)
    await AuditService(db).record(
        actor_type="user",
        action="auth.login",
        detail={"username": body.username},
        ip_address=_client_ip(request),
    )
    return success({"access_token": token, "token_type": "bearer"})


@router.post("/device/token", summary="Tukar kredensial device dengan token")
async def device_token(body: DeviceLoginRequest, request: Request, db: DbSession) -> dict[str, Any]:
    """Token yang diterbitkan **terikat pada satu ship_id**.

    Itulah yang mencegah satu kapal menyetor data atas nama kapal lain, baik
    karena salah konfigurasi maupun karena kredensialnya dicuri.
    """
    tokens = request.app.state.tokens
    token, device_id, ship_id = await IdentityService(db, tokens).authenticate_device(
        body.client_id, body.secret
    )
    await AuditService(db).record(
        actor_type="device",
        actor_id=device_id,
        action="auth.device_token",
        resource_type="ship",
        resource_id=str(ship_id),
        ip_address=_client_ip(request),
    )
    return success(
        {
            "access_token": token,
            "token_type": "bearer",
            "device_id": str(device_id),
            "ship_id": str(ship_id),
        }
    )


@router.get("/me", summary="Principal saat ini")
async def me(user: CurrentUser) -> dict[str, Any]:
    return success(
        {
            "user_id": str(user.user_id),
            "username": user.username,
            "roles": sorted(user.roles),
        }
    )


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None
