"""/api/v1/ships, /api/v1/devices, /api/v1/config."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from fleetview_central.http.envelope import success
from fleetview_central.modules.fleet.service import FleetService
from fleetview_central.modules.ops.service import AuditService
from fleetview_central.platform.deps import CurrentUser, DbSession

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
