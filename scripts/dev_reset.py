"""Hapus jejak kapal simulasi di central.

Dipanggil `scripts/dev.sh reset`, bersama penghapusan `.devdata`.

**Keduanya harus dilakukan bersamaan.** Menghapus outbox kapal saja membuat
agent memulai nomor sequence dari 1 lagi, sementara central masih menyimpan
ledger batch untuk rentang sequence itu. Kiriman berikutnya melanggar
`uq_batch_range` dan ditolak `ingest.sequence_reused` — perilaku yang benar,
tetapi membingungkan kalau muncul hanya karena melakukan reset.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

from sqlalchemy import delete, select

from fleetview_central.modules.fleet.models import Device, Sensor, Ship
from fleetview_central.modules.identity.models import DeviceCredential
from fleetview_central.modules.ingest.models import ShipSyncState, SyncBatch
from fleetview_central.platform.db import Database
from fleetview_central.settings import load_settings

SHIP_ID = UUID("11111111-1111-1111-1111-111111111111")


async def main() -> int:
    settings = load_settings()
    db = Database(settings.postgres_dsn)
    async with db.session() as session:
        ship = (await session.execute(select(Ship).where(Ship.id == SHIP_ID))).scalar_one_or_none()
        if ship is None:
            print("  tidak ada kapal simulasi di central")
            await db.dispose()
            return 0

        # Urutannya mengikuti arah foreign key: anak lebih dulu, induk terakhir.
        for stmt in (
            delete(SyncBatch).where(SyncBatch.ship_id == SHIP_ID),
            delete(ShipSyncState).where(ShipSyncState.ship_id == SHIP_ID),
            delete(Sensor).where(Sensor.ship_id == SHIP_ID),
            delete(DeviceCredential).where(DeviceCredential.ship_id == SHIP_ID),
            delete(Device).where(Device.ship_id == SHIP_ID),
            delete(Ship).where(Ship.id == SHIP_ID),
        ):
            await session.execute(stmt)
        await session.commit()
    await db.dispose()
    print("  ledger dan pendaftaran kapal simulasi dihapus dari central")
    # Telemetry di InfluxDB sengaja dibiarkan: ia bertag ship_id, jadi tidak
    # mengganggu apa pun, dan menghapusnya butuh operasi delete yang lambat.
    # Data itu akan tertimpa sendiri karena identitas titiknya sama.
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
