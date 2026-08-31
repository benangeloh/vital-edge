"""OutboxSink — write barrier.

Ini titik di mana sebuah pembacaan berhenti menjadi sesuatu yang ada di memori
dan mulai menjadi sesuatu yang ada di dunia.

Urutannya penting dan tidak boleh dibalik:

    collector -> OutboxSink (fsync, durable)  <- BARRIER
                     |
         +-----------+-----------+
         v                       v
    StorageWriter            SyncEngine
    (InfluxDB, hilir)        (central, hilir)

Sekali `append()` kembali tanpa exception, data selamat dari mati listrik.
Semua yang terjadi setelahnya boleh gagal dan dicoba ulang. Itulah alasan
InfluxDB tidak berada di jalur kritis akuisisi: InfluxDB yang sedang compaction
tidak menghentikan apa pun.
"""

from __future__ import annotations

import asyncio

from fleetview_common import get_logger
from fleetview_contracts import SyncPriority, TelemetryRecord
from fleetview_edge.outbox.store import OutboxStore

__all__ = ["OutboxSink"]

log = get_logger(__name__)


class OutboxSink:
    """Sink untuk Collector yang menulis durable lalu meneruskan ke hilir.

    Args:
        store: outbox.
        downstream: sink hilir, biasanya StorageWriter. Kegagalannya tidak
            pernah dinaikkan — data sudah aman di outbox.
        priority_for: pemetaan record ke prioritas sinkronisasi.
    """

    def __init__(
        self,
        store: OutboxStore,
        *,
        downstream: object = None,
        default_priority: SyncPriority = SyncPriority.RAW,
    ) -> None:
        self._store = store
        self._downstream = downstream
        self._default_priority = default_priority
        self._appended = 0

    @property
    def appended(self) -> int:
        return self._appended

    async def __call__(self, records: list[TelemetryRecord]) -> None:
        if not records:
            return

        # fsync terjadi di sini. Dijalankan di thread supaya loop akuisisi tidak
        # ikut terblokir menunggu disk — pada SD card yang lambat, fsync bisa
        # memakan puluhan milidetik.
        await asyncio.to_thread(self._store.append, records, priority=self._default_priority)
        self._appended += len(records)

        # Mulai titik ini data sudah aman. Apa pun yang gagal di bawah bukan
        # kehilangan data, hanya keterlambatan.
        if self._downstream is not None and callable(self._downstream):
            try:
                await self._downstream(records)
            except Exception:
                log.exception(
                    "outbox.downstream_failed",
                    records=len(records),
                    note="data sudah durable di outbox; ini hanya keterlambatan",
                )
