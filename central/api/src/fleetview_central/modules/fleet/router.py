"""/api/v1/ships, /api/v1/devices, /api/v1/config."""

from __future__ import annotations

import secrets
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from fleetview_central.http.envelope import success
from fleetview_central.modules.fleet.service import FleetService
from fleetview_central.modules.identity.service import IdentityService
from fleetview_central.modules.ops.service import AuditService
from fleetview_central.platform.deps import CurrentUser, DbSession
from fleetview_common import ValidationError

router = APIRouter(prefix="/api/v1", tags=["fleet"])


class CreateShip(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    slug: Annotated[str, Field(min_length=1, max_length=64)]
    imo_number: str | None = None


class CreateDevice(BaseModel):
    ship_id: UUID
    name: Annotated[str, Field(min_length=1, max_length=128)]
    hardware: str | None = None
    field_device: str | None = None


class OnboardShip(BaseModel):
    """Satu formulir untuk seluruh proses onboarding kapal baru."""

    name: Annotated[str, Field(min_length=1, max_length=128)]
    slug: Annotated[str, Field(min_length=1, max_length=64)]
    imo_number: str | None = None
    device_name: Annotated[str, Field(min_length=1, max_length=128)] = "Edge Pi #1"
    hardware: str | None = "Raspberry Pi 4B"
    client_id: Annotated[str | None, Field(max_length=64)] = None
    """Kosongkan agar diturunkan dari slug, mis. SHIP-071 -> ship-071."""


class CreateConfig(BaseModel):
    version: Annotated[str, Field(min_length=1, max_length=64)]
    payload: dict[str, Any]
    notes: str | None = None


@router.get("/ships", summary="Daftar armada")
async def list_ships(
    user: CurrentUser,
    db: DbSession,
    q: Annotated[str | None, Query(description="cari berdasarkan nama")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    """Tampilan armada. PostgreSQL saja — InfluxDB tidak ikut di jalur ini."""
    _ = user
    ships = await FleetService(db).list_ships(query=q, limit=limit, offset=offset)
    return success(ships, count=len(ships), limit=limit, offset=offset)


@router.post("/ships", status_code=201, summary="Daftarkan kapal")
async def create_ship(body: CreateShip, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    user.require_role("admin", "fleet_manager")
    ship = await FleetService(db).create_ship(
        name=body.name, slug=body.slug, imo_number=body.imo_number
    )
    await AuditService(db).record(
        actor_type="user",
        actor_id=user.user_id,
        action="fleet.ship_created",
        resource_type="ship",
        resource_id=str(ship.id),
        detail={"name": ship.name},
    )
    return success({"ship_id": str(ship.id), "name": ship.name, "slug": ship.slug})


@router.post("/ships/onboard", status_code=201, summary="Onboard kapal baru sekaligus")
async def onboard_ship(
    body: OnboardShip, user: CurrentUser, request: Request, db: DbSession
) -> dict[str, Any]:
    """Daftarkan kapal, perangkatnya, dan kredensialnya dalam satu transaksi.

    Menggantikan tiga panggilan berurutan yang, kalau gagal di tengah,
    meninggalkan kapal tanpa perangkat atau perangkat tanpa kredensial — keadaan
    yang harus dibereskan manual lewat SSH ke server.

    `client_secret` di jawaban **hanya dikembalikan sekali ini**. Di basis data
    ia hanya ada sebagai hash Argon2id. Kalau hilang, terbitkan yang baru dan
    cabut yang lama.
    """
    user.require_role("admin", "fleet_manager")
    fleet = FleetService(db)

    if await fleet.ship_by_slug(body.slug) is not None:
        raise ValidationError(
            f"slug {body.slug!r} sudah dipakai kapal lain",
            code="fleet.slug_taken",
            details={"slug": body.slug},
        )

    client_id = body.client_id or body.slug.lower()
    identity = IdentityService(db, request.app.state.tokens)
    if await identity.device_credential(client_id) is not None:
        raise ValidationError(
            f"client_id {client_id!r} sudah dipakai",
            code="fleet.client_id_taken",
            details={"client_id": client_id},
        )

    ship = await fleet.create_ship(name=body.name, slug=body.slug, imo_number=body.imo_number)
    device = await fleet.create_device(
        ship_id=ship.id, name=body.device_name, hardware=body.hardware
    )
    # 32 byte acak, bukan kata sandi yang dipilih manusia: kredensial ini tidak
    # pernah diketik, hanya disalin ke secrets.env di Pi.
    secret = secrets.token_urlsafe(32)
    await identity.create_device_credential(
        device_id=device.id, ship_id=ship.id, client_id=client_id, secret=secret
    )

    await AuditService(db).record(
        actor_type="user",
        actor_id=user.user_id,
        action="fleet.ship_onboarded",
        resource_type="ship",
        resource_id=str(ship.id),
        # Rahasianya TIDAK ikut dicatat. Audit log dibaca jauh lebih luas
        # daripada yang boleh melihat kredensial perangkat.
        detail={"slug": ship.slug, "device_id": str(device.id), "client_id": client_id},
        ip_address=_client_ip(request),
    )

    return success(
        {
            "ship_id": str(ship.id),
            "slug": ship.slug,
            "name": ship.name,
            "device_id": str(device.id),
            "client_id": client_id,
            "client_secret": secret,
        },
        secret_shown_once=True,
    )


@router.get("/ships/{ship_id}", summary="Detail kapal")
async def get_ship(ship_id: UUID, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    _ = user
    service = FleetService(db)
    ship = await service.get_ship(ship_id)
    config = await service.active_config(ship_id)
    return success(
        {
            "ship_id": str(ship.id),
            "name": ship.name,
            "slug": ship.slug,
            "imo_number": ship.imo_number,
            "call_sign": ship.call_sign,
            "is_active": ship.is_active,
            "devices": [
                {
                    "device_id": str(d.id),
                    "name": d.name,
                    "hardware": d.hardware,
                    "field_device": d.field_device,
                    "agent_version": d.agent_version,
                }
                for d in await service.list_devices(ship_id)
            ],
            "active_config_version": config.version if config else None,
        }
    )


@router.get("/ships/{ship_id}/sensors", summary="Registry sensor kapal")
async def list_sensors(ship_id: UUID, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    """Termasuk sensor yang didaftarkan otomatis saat pertama terlihat —
    statusnya `unregistered` sampai operator memberinya nama."""
    _ = user
    sensors = await FleetService(db).list_sensors(ship_id)
    return success(
        [
            {
                "sensor_id": s.sensor_id,
                "metric": s.metric,
                "unit": s.unit,
                "status": s.status,
                "display_name": s.display_name,
                "tags": s.tags,
                "last_seen_at": s.last_seen_at.isoformat() if s.last_seen_at else None,
            }
            for s in sensors
        ]
    )


@router.get("/devices", summary="Daftar perangkat")
async def list_devices(
    user: CurrentUser, db: DbSession, ship_id: UUID | None = None
) -> dict[str, Any]:
    _ = user
    devices = await FleetService(db).list_devices(ship_id)
    return success(
        [
            {
                "device_id": str(d.id),
                "ship_id": str(d.ship_id),
                "name": d.name,
                "hardware": d.hardware,
                "field_device": d.field_device,
                "agent_version": d.agent_version,
                "is_active": d.is_active,
            }
            for d in devices
        ]
    )


@router.post("/devices", status_code=201, summary="Daftarkan perangkat")
async def create_device(body: CreateDevice, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    user.require_role("admin", "fleet_manager")
    device = await FleetService(db).create_device(
        ship_id=body.ship_id,
        name=body.name,
        hardware=body.hardware,
        field_device=body.field_device,
    )
    await AuditService(db).record(
        actor_type="user",
        actor_id=user.user_id,
        action="fleet.device_created",
        resource_type="device",
        resource_id=str(device.id),
    )
    return success({"device_id": str(device.id), "ship_id": str(device.ship_id)})


@router.get("/devices/{device_id}/credentials", summary="Kredensial perangkat")
async def list_credentials(
    device_id: UUID, user: CurrentUser, request: Request, db: DbSession
) -> dict[str, Any]:
    """Daftar kredensial sebuah perangkat. **Tanpa rahasia** — hanya statusnya."""
    user.require_role("admin", "fleet_manager")
    identity = IdentityService(db, request.app.state.tokens)
    creds = await identity.credentials_for_device(device_id)
    return success(
        [
            {
                "client_id": c.client_id,
                "is_active": c.is_active and c.revoked_at is None,
                "created_at": c.created_at.isoformat() if c.created_at else None,
                "revoked_at": c.revoked_at.isoformat() if c.revoked_at else None,
            }
            for c in creds
        ]
    )


@router.post("/devices/{device_id}/credentials", status_code=201, summary="Terbitkan kredensial")
async def issue_credential(
    device_id: UUID, user: CurrentUser, request: Request, db: DbSession
) -> dict[str, Any]:
    """Terbitkan kredensial baru untuk perangkat yang sudah ada.

    Dipakai saat Raspberry Pi diganti, atau saat rahasia lama hilang. Kredensial
    lama TIDAK otomatis dicabut: perangkat lama mungkin masih menyetor sisa
    backlog-nya, dan mencabutnya seketika akan mengunci data itu di kapal.
    Cabut secara terpisah setelah backlog habis.
    """
    user.require_role("admin", "fleet_manager")
    device = await FleetService(db).device_by_id(device_id)
    if device is None:
        raise ValidationError(
            "perangkat tidak ditemukan",
            code="fleet.device_not_found",
            details={"device_id": str(device_id)},
        )

    identity = IdentityService(db, request.app.state.tokens)
    client_id = f"{device.id.hex[:8]}-{secrets.token_hex(3)}"
    secret = secrets.token_urlsafe(32)
    await identity.create_device_credential(
        device_id=device.id, ship_id=device.ship_id, client_id=client_id, secret=secret
    )
    await AuditService(db).record(
        actor_type="user",
        actor_id=user.user_id,
        action="fleet.credential_issued",
        resource_type="device",
        resource_id=str(device_id),
        detail={"client_id": client_id},
        ip_address=_client_ip(request),
    )
    return success({"client_id": client_id, "client_secret": secret}, secret_shown_once=True)


@router.post("/credentials/{client_id}/revoke", summary="Cabut kredensial")
async def revoke_credential(
    client_id: str, user: CurrentUser, request: Request, db: DbSession
) -> dict[str, Any]:
    """Cabut kredensial perangkat.

    Berlaku pada penerbitan token berikutnya; token yang sudah terbit tetap
    berlaku sampai kedaluwarsa. Barisnya ditandai, bukan dihapus — audit log dan
    penelusuran insiden merujuk padanya.
    """
    user.require_role("admin", "fleet_manager")
    identity = IdentityService(db, request.app.state.tokens)
    cred = await identity.revoke_device_credential(client_id)
    if cred is None:
        raise ValidationError(
            "kredensial tidak ditemukan",
            code="fleet.credential_not_found",
            details={"client_id": client_id},
        )
    await AuditService(db).record(
        actor_type="user",
        actor_id=user.user_id,
        action="fleet.credential_revoked",
        resource_type="device",
        resource_id=str(cred.device_id),
        detail={"client_id": client_id},
        ip_address=_client_ip(request),
    )
    return success({"client_id": client_id, "revoked": True})


@router.get("/config/{ship_id}", summary="Riwayat versi konfigurasi")
async def list_configs(ship_id: UUID, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    _ = user
    versions = await FleetService(db).list_config_versions(ship_id)
    return success(
        [
            {
                "version": c.version,
                "revision": c.revision,
                "is_active": c.is_active,
                "notes": c.notes,
                "created_at": c.created_at.isoformat(),
            }
            for c in versions
        ]
    )


@router.put("/config/{ship_id}", summary="Buat versi konfigurasi baru")
async def create_config(
    ship_id: UUID, body: CreateConfig, user: CurrentUser, db: DbSession
) -> dict[str, Any]:
    """Konfigurasi tidak pernah diedit di tempat — setiap perubahan membuat versi
    baru, dan versi lama tetap ada."""
    user.require_role("admin", "fleet_manager")
    config = await FleetService(db).create_config_version(
        ship_id=ship_id,
        version=body.version,
        payload=body.payload,
        notes=body.notes,
        created_by=user.user_id,
    )
    await AuditService(db).record(
        actor_type="user",
        actor_id=user.user_id,
        action="fleet.config_created",
        resource_type="ship",
        resource_id=str(ship_id),
        detail={"version": body.version},
    )
    return success({"version": config.version, "revision": config.revision})


def _client_ip(request: Request) -> str | None:
    """Alamat klien untuk audit log; sama seperti di modul identity.

    Di belakang reverse proxy, `request.client.host` selalu loopback. Entri
    terakhir `X-Forwarded-For` adalah yang benar-benar dilihat proxy; entri
    sebelumnya berasal dari klien dan bisa dipalsukan.
    """
    settings = getattr(request.app.state, "settings", None)
    if getattr(settings, "trust_proxy_headers", False):
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            candidate = forwarded.rsplit(",", 1)[-1].strip()
            if candidate:
                return candidate
    return request.client.host if request.client else None
