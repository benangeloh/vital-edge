"""Layanan armada: kapal, perangkat, sensor, dan konfigurasi berversi."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from fleetview_central.modules.fleet.models import Device, Sensor, Ship, ShipConfig
from fleetview_central.modules.ingest.models import ShipSyncState
from fleetview_common import ValidationError, now_utc

__all__ = ["FleetService"]

#: Sebuah kapal dianggap offline setelah sekian detik tanpa kabar. Nilainya
#: harus lebih longgar daripada interval heartbeat — kapal yang sedang mengirim
#: batch besar lewat satelit lambat tidak boleh dianggap mati.
OFFLINE_AFTER_SECONDS = 300


class FleetService:
    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    async def list_ships(
        self, *, query: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        """Tampilan armada.

        **PostgreSQL saja — InfluxDB tidak ikut di jalur ini.** Status, lag sync,
        dan jumlah pending semuanya sudah didenormalisasi oleh jalur ingest,
        sehingga tampilan 70 kapal hanya butuh satu query ber-index berapa pun
        besar volume telemetry-nya.
        """
        stmt = (
            select(Ship, ShipSyncState)
            .outerjoin(ShipSyncState, ShipSyncState.ship_id == Ship.id)
            .order_by(Ship.name)
            .limit(limit)
            .offset(offset)
        )
        if query:
            stmt = stmt.where(Ship.name.ilike(f"%{query}%"))

        rows = await self._db.execute(stmt)
        now = now_utc()
        out: list[dict[str, Any]] = []
        for ship, state in rows:
            last_seen = state.last_batch_received_at if state else None
            stale = last_seen is None or (now - last_seen).total_seconds() > OFFLINE_AFTER_SECONDS
            out.append(
                {
                    "ship_id": str(ship.id),
                    "name": ship.name,
                    "slug": ship.slug,
                    "imo_number": ship.imo_number,
                    "is_active": ship.is_active,
                    "connection_state": "offline"
                    if stale
                    else (state.connection_state if state else "offline"),
                    "last_batch_received_at": last_seen.isoformat() if last_seen else None,
                    "last_telemetry_timestamp": (
                        state.last_telemetry_timestamp.isoformat()
                        if state and state.last_telemetry_timestamp
                        else None
                    ),
                    "last_contiguous_sequence": state.last_contiguous_sequence if state else 0,
                    "has_gap": (
                        state.highest_sequence_seen > state.last_contiguous_sequence
                        if state
                        else False
                    ),
                    "pending_estimate": state.pending_estimate if state else None,
                    "agent_version": state.agent_version if state else None,
                    "config_version": state.config_version if state else None,
                    "total_records": state.total_records if state else 0,
                }
            )
        return out

    async def get_ship(self, ship_id: UUID) -> Ship:
        ship = await self._db.get(Ship, ship_id)
        if ship is None:
            raise ValidationError(f"kapal {ship_id} tidak ditemukan", code="fleet.ship_not_found")
        return ship

    async def create_ship(self, *, name: str, slug: str, imo_number: str | None = None) -> Ship:
        ship = Ship(name=name, slug=slug, imo_number=imo_number)
        self._db.add(ship)
        await self._db.flush()
        self._db.add(ShipSyncState(ship_id=ship.id))
        await self._db.flush()
        return ship

    async def ship_by_slug(self, slug: str) -> Ship | None:
        """Slug adalah nama direktori paket export USB dan pengenal yang dipakai
        manusia. Bentrokan harus ditolak sebelum apa pun dibuat, bukan dibiarkan
        muncul sebagai IntegrityError di tengah transaksi."""
        return (await self._db.execute(select(Ship).where(Ship.slug == slug))).scalar_one_or_none()

    async def ship_by_id(self, ship_id: UUID) -> Ship | None:
        return await self._db.get(Ship, ship_id)

    async def device_by_id(self, device_id: UUID) -> Device | None:
        return await self._db.get(Device, device_id)

    async def create_device(
        self,
        *,
        ship_id: UUID,
        name: str,
        hardware: str | None = None,
        field_device: str | None = None,
    ) -> Device:
        await self.get_ship(ship_id)
        device = Device(ship_id=ship_id, name=name, hardware=hardware, field_device=field_device)
        self._db.add(device)
        await self._db.flush()
        return device

    async def list_devices(self, ship_id: UUID | None = None) -> list[Device]:
        stmt = select(Device)
        if ship_id is not None:
            stmt = stmt.where(Device.ship_id == ship_id)
        return list(await self._db.scalars(stmt.order_by(Device.name)))

    async def list_sensors(self, ship_id: UUID) -> list[Sensor]:
        return list(
            await self._db.scalars(
                select(Sensor).where(Sensor.ship_id == ship_id).order_by(Sensor.sensor_id)
            )
        )

    # -- konfigurasi --------------------------------------------------------

    async def active_config(self, ship_id: UUID) -> ShipConfig | None:
        config: ShipConfig | None = await self._db.scalar(
            select(ShipConfig).where(ShipConfig.ship_id == ship_id, ShipConfig.is_active.is_(True))
        )
        return config

    async def create_config_version(
        self,
        *,
        ship_id: UUID,
        version: str,
        payload: dict[str, Any],
        notes: str | None = None,
        created_by: UUID | None = None,
    ) -> ShipConfig:
        """Buat versi konfigurasi baru dan aktifkan.

        Konfigurasi **tidak pernah diedit di tempat**. Versi lama tetap ada,
        sehingga saat sebuah kapal berperilaku aneh kita bisa melihat persis
        konfigurasi apa yang dijalankannya saat itu.
        """
        await self.get_ship(ship_id)
        existing = await self._db.scalar(
            select(ShipConfig).where(ShipConfig.ship_id == ship_id, ShipConfig.version == version)
        )
        if existing is not None:
            raise ValidationError(
                f"versi konfigurasi {version!r} sudah ada untuk kapal ini",
                code="fleet.config_version_exists",
            )

        await self._db.execute(
            update(ShipConfig).where(ShipConfig.ship_id == ship_id).values(is_active=False)
        )
        revision = (
            await self._db.scalar(
                select(func.count()).select_from(ShipConfig).where(ShipConfig.ship_id == ship_id)
            )
        ) or 0
        config = ShipConfig(
            ship_id=ship_id,
            version=version,
            payload=payload,
            is_active=True,
            notes=notes,
            created_by=created_by,
            revision=revision + 1,
        )
        self._db.add(config)
        await self._db.flush()
        return config

    async def list_config_versions(self, ship_id: UUID) -> list[ShipConfig]:
        return list(
            await self._db.scalars(
                select(ShipConfig)
                .where(ShipConfig.ship_id == ship_id)
                .order_by(ShipConfig.revision.desc())
            )
        )
