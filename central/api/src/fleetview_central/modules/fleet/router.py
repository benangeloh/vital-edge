"""/api/v1/ships, /api/v1/devices, /api/v1/config."""

from __future__ import annotations

import secrets
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from fleetview_central.http.envelope import success
from fleetview_central.modules.fleet.models import Device, Ship
from fleetview_central.modules.fleet.service import FleetService
from fleetview_central.modules.identity.service import IdentityService
from fleetview_central.modules.ops.service import AuditService
from fleetview_central.platform.deps import CurrentUser, DbSession
from fleetview_central.platform.secretbox import SecretBox
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
    device_name: Annotated[str, Field(min_length=1, max_length=128)] = "Perangkat Edge #1"
    hardware: str | None = "Raspberry Pi 4B"
    client_id: Annotated[str | None, Field(max_length=64)] = None
    """Kosongkan agar diturunkan dari slug, mis. SHIP-071 -> ship-071."""


class UpdateShip(BaseModel):
    """Hanya yang dikirim yang berubah. Kode kapal sengaja tidak ada di sini."""

    name: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    imo_number: Annotated[str | None, Field(max_length=16)] = None


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
    include_inactive: Annotated[
        bool, Query(description="sertakan kapal yang sudah dinonaktifkan")
    ] = False,
) -> dict[str, Any]:
    """Tampilan armada. PostgreSQL saja — InfluxDB tidak ikut di jalur ini."""
    _ = user
    ships = await FleetService(db).list_ships(
        query=q, limit=limit, offset=offset, include_inactive=include_inactive
    )
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
    identity = _identity(db, request)
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
            "recoverable": identity.can_recover,
        },
        secret_shown_once=not identity.can_recover,
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


@router.patch("/ships/{ship_id}", summary="Ubah nama atau nomor IMO kapal")
async def update_ship(
    ship_id: UUID, body: UpdateShip, user: CurrentUser, request: Request, db: DbSession
) -> dict[str, Any]:
    """Kode kapal tidak bisa diubah: ia nama folder ekspor USB dan pengenal di ledger."""
    user.require_role("admin", "fleet_manager")
    changes = {k: getattr(body, k) for k in body.model_fields_set}
    if changes.get("name") is None:
        changes.pop("name", None)
    ship = await FleetService(db).update_ship(ship_id, changes)
    await AuditService(db).record(
        actor_type="user",
        actor_id=user.user_id,
        action="fleet.ship_updated",
        resource_type="ship",
        resource_id=str(ship.id),
        detail={"fields": sorted(changes)},
        ip_address=_client_ip(request),
    )
    return success({"ship_id": str(ship.id), "name": ship.name, "imo_number": ship.imo_number})


@router.get("/ships/{ship_id}/credential", summary="Buka kredensial pasang kapal")
async def get_ship_credential(
    ship_id: UUID, user: CurrentUser, request: Request, db: DbSession, response: Response
) -> dict[str, Any]:
    """Kredensial aktif kapal, lengkap dengan rahasianya, untuk menyusun skrip pasang.

    Rahasianya dibuka dari salinan terenkripsi, bukan dari hash. Kredensial yang
    dibuat sebelum fitur ini tidak punya salinan itu; jawabannya
    `fleet.credential_not_recoverable` dan jalan keluarnya `POST` di path yang sama.
    Setiap pembukaan dicatat di audit log, tanpa rahasianya.
    """
    user.require_role("admin", "fleet_manager")
    fleet = FleetService(db)
    ship = await fleet.get_ship(ship_id)
    device = await fleet.primary_device(ship_id)
    identity = _identity(db, request)

    cred = await identity.active_credential(device.id)
    if cred is None:
        raise ValidationError(
            "kapal ini tidak punya kredensial aktif. Terbitkan kredensial baru.",
            code="fleet.no_active_credential",
            details={"ship_id": str(ship_id)},
        )
    secret = identity.reveal_secret(cred)

    await AuditService(db).record(
        actor_type="user",
        actor_id=user.user_id,
        action="fleet.credential_viewed",
        resource_type="ship",
        resource_id=str(ship.id),
        detail={"client_id": cred.client_id},
        ip_address=_client_ip(request),
    )
    response.headers["Cache-Control"] = "no-store"
    return success(_handover(ship, device, cred.client_id, secret, recoverable=True))


@router.post(
    "/ships/{ship_id}/credential", status_code=201, summary="Terbitkan kredensial baru untuk kapal"
)
async def issue_ship_credential(
    ship_id: UUID, user: CurrentUser, request: Request, db: DbSession, response: Response
) -> dict[str, Any]:
    """Untuk kapal yang kredensialnya tidak bisa dibuka lagi, atau saat perangkat diganti.

    Kredensial lama TIDAK otomatis dicabut: perangkat lama mungkin masih menyetor
    sisa backlog-nya. Cabut terpisah lewat `/credentials/{client_id}/revoke`.
    """
    user.require_role("admin", "fleet_manager")
    fleet = FleetService(db)
    ship = await fleet.get_ship(ship_id)
    device = await fleet.primary_device(ship_id)
    identity = _identity(db, request)
    client_id, secret = await _issue(identity, device)

    await AuditService(db).record(
        actor_type="user",
        actor_id=user.user_id,
        action="fleet.credential_issued",
        resource_type="ship",
        resource_id=str(ship.id),
        detail={"client_id": client_id, "device_id": str(device.id)},
        ip_address=_client_ip(request),
    )
    response.headers["Cache-Control"] = "no-store"
    return success(_handover(ship, device, client_id, secret, recoverable=identity.can_recover))


@router.post("/ships/{ship_id}/decommission", summary="Nonaktifkan kapal")
async def decommission_ship(
    ship_id: UUID, user: CurrentUser, request: Request, db: DbSession
) -> dict[str, Any]:
    """Cabut semua akses kapal dan sembunyikan dari daftar. Datanya tetap disimpan.

    Baris kapal tidak dihapus karena ledger batch dan telemetry merujuk padanya
    (docs/operations/08-ship-decommissioning.md). Aman dipanggil berulang.
    """
    user.require_role("admin", "fleet_manager")
    ship = await FleetService(db).decommission_ship(ship_id)
    revoked = await _identity(db, request).revoke_all_for_ship(ship_id)
    await AuditService(db).record(
        actor_type="user",
        actor_id=user.user_id,
        action="fleet.ship_decommissioned",
        resource_type="ship",
        resource_id=str(ship.id),
        detail={"slug": ship.slug, "revoked_credentials": revoked},
        ip_address=_client_ip(request),
    )
    return success(
        {
            "ship_id": str(ship.id),
            "slug": ship.slug,
            "is_active": False,
            "revoked_credentials": revoked,
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
    identity = _identity(db, request)
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

    client_id, secret = await _issue(_identity(db, request), device)
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
    identity = _identity(db, request)
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


def _identity(db: AsyncSession, request: Request) -> IdentityService:
    """Satu tempat membuat IdentityService, supaya tidak ada endpoint yang lupa
    memberinya kunci enkripsi dan diam-diam menerbitkan kredensial yang tidak
    bisa dibuka lagi."""
    secretbox: SecretBox | None = getattr(request.app.state, "secretbox", None)
    return IdentityService(db, request.app.state.tokens, secretbox)


async def _issue(identity: IdentityService, device: Device) -> tuple[str, str]:
    # 32 byte acak, bukan kata sandi yang dipilih manusia: kredensial ini tidak
    # pernah diketik, hanya disalin ke secrets.env di komputer kapal.
    client_id = f"{device.id.hex[:8]}-{secrets.token_hex(3)}"
    secret = secrets.token_urlsafe(32)
    await identity.create_device_credential(
        device_id=device.id, ship_id=device.ship_id, client_id=client_id, secret=secret
    )
    return client_id, secret


def _handover(
    ship: Ship, device: Device, client_id: str, secret: str, *, recoverable: bool
) -> dict[str, str | bool]:
    """Bentuk yang sama dengan hasil onboarding, supaya dialog serah-terima
    kredensial di dashboard bisa dipakai untuk keduanya."""
    return {
        "ship_id": str(ship.id),
        "slug": ship.slug,
        "name": ship.name,
        "device_id": str(device.id),
        "client_id": client_id,
        "client_secret": secret,
        # Dashboard memakai ini untuk memilih kalimat yang jujur: "bisa dibuka
        # lagi" atau "hanya tampil sekali".
        "recoverable": recoverable,
    }


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
