"""StorageWriter — sink yang dipanggil Collector.

Komponen ini punya satu tugas yang tidak boleh dilanggar: **tidak pernah
melempar exception ke Collector.**

Alasannya bukan kerapian, melainkan operasional. Collector adalah loop akuisisi
yang berjalan berbulan-bulan di kapal tanpa ada yang mengawasi. Kalau kegagalan
penulisan database sampai naik ke loop itu dan menghentikannya, kapal berhenti
mengumpulkan data karena masalah pada penyimpanan — padahal keduanya seharusnya
saling bebas. InfluxDB yang sedang compaction tidak boleh membuat kapal buta.
"""

from __future__ import annotations

import asyncio

from fleetview_common import FleetViewError, get_logger
from fleetview_contracts import TelemetryRecord
from fleetview_edge.storage.base import (
    StorageHealth,
    StorageRejectedError,
    StorageState,
    TelemetryStore,
)
from fleetview_edge.storage.buffer import BoundedRetryBuffer

__all__ = ["StorageWriter"]

log = get_logger(__name__)


class StorageWriter:
    """Menerima record dari Collector, menulisnya, menyangga saat gagal.

    Args:
        store: penyimpanan tujuan.
        buffer_max_records: kapasitas buffer percobaan ulang.
        retry_batch_size: berapa record yang dicoba ulang sekali jalan.
        drain_on_write: coba kosongkan buffer setiap kali ada penulisan baru.
            Cukup untuk sekarang; Phase 4 memindahkannya ke worker tersendiri.
    """

    def __init__(
        self,
        store: TelemetryStore,
        *,
        buffer_max_records: int = 10_000,
        retry_batch_size: int = 500,
        drain_on_write: bool = True,
    ) -> None:
        self._store = store
        self._buffer = BoundedRetryBuffer(buffer_max_records)
        self._retry_batch = retry_batch_size
        self._drain_on_write = drain_on_write
        self._lock = asyncio.Lock()
        self._written = 0
        self._rejected = 0
        self._buffered_total = 0

    @property
    def buffered(self) -> int:
        return len(self._buffer)

    @property
    def dropped(self) -> int:
        return self._buffer.dropped

    @property
    def written(self) -> int:
        return self._written

    @property
    def rejected(self) -> int:
        """Record yang ditolak permanen dan sengaja tidak diulang."""
        return self._rejected

    async def __call__(self, records: list[TelemetryRecord]) -> None:
        """Sink untuk Collector. **Tidak pernah melempar.**"""
        if not records:
            return
        try:
            await self._handle(records)
        except Exception:
            # Jaring pengaman terakhir. Kalau sampai ke sini, ada bug di
            # penanganan error kita sendiri — tetap saja Collector tidak boleh
            # ikut mati karenanya.
            log.exception("storage.writer_unexpected_error", records=len(records))

    async def _handle(self, records: list[TelemetryRecord]) -> None:
        async with self._lock:
            if self._drain_on_write:
                await self._drain()

            # Kalau masih ada tunggakan, record baru ikut mengantre di
            # belakangnya — kalau tidak, urutannya jadi kacau dan grafik
            # menampilkan data lama setelah data baru.
            if len(self._buffer):
                self._buffered_total += len(records)
                self._buffer.extend(records)
                return

            await self._write_or_buffer(records)

    async def _write_or_buffer(self, records: list[TelemetryRecord]) -> None:
        try:
            await self._store.write(records)
            self._written += len(records)
        except StorageRejectedError as exc:
            # Ditolak permanen. Membuangnya memang kehilangan data, tetapi
            # mengulangnya akan menyumbat buffer selamanya dan membuat SEMUA
            # data berikutnya hilang. Dicatat sekeras mungkin.
            self._rejected += len(records)
            log.error(
                "storage.records_rejected",
                records=len(records),
                code=exc.code,
                error=exc.message,
                rejected_total=self._rejected,
                note="DATA DIBUANG: penyimpanan menolak permanen, mengulang tidak menolong",
                details=exc.details,
            )
        except FleetViewError as exc:
            self._buffered_total += len(records)
            self._buffer.extend(records)
            log.warning(
                "storage.write_buffered",
                records=len(records),
                buffered=len(self._buffer),
                code=exc.code,
                error=exc.message,
            )

    async def _drain(self) -> None:
        """Coba tulis ulang isi buffer. Berhenti pada kegagalan pertama."""
        while len(self._buffer):
            batch = self._buffer.take(self._retry_batch)
            try:
                await self._store.write(batch)
                self._written += len(batch)
            except StorageRejectedError as exc:
                self._rejected += len(batch)
                log.error(
                    "storage.buffered_records_rejected",
                    records=len(batch),
                    code=exc.code,
                    rejected_total=self._rejected,
                    note="DATA DIBUANG: ditolak permanen saat pengosongan buffer",
                )
            except FleetViewError:
                # Masih belum pulih. Kembalikan ke depan antrean dan berhenti;
                # mencoba batch berikutnya hanya membuang waktu.
                self._buffer.put_back(batch)
                return

        if self._buffered_total:
            log.info("storage.buffer_drained", recovered_total=self._buffered_total)
            self._buffered_total = 0

    async def flush(self) -> int:
        """Paksa pengosongan buffer. Kembalikan sisa yang belum tertulis."""
        async with self._lock:
            await self._drain()
            return len(self._buffer)

    async def health(self) -> StorageHealth:
        """Kondisi penyimpanan, digabung dengan kondisi buffer."""
        try:
            base = await self._store.health()
        except Exception as exc:
            base = StorageHealth(state=StorageState.UNKNOWN, detail=str(exc))

        return StorageHealth(
            state=base.state,
            reachable=base.reachable,
            last_success_us=base.last_success_us,
            consecutive_failures=base.consecutive_failures,
            buffered_records=len(self._buffer),
            dropped_records=self._buffer.dropped,
            detail=base.detail,
            counters={
                **base.counters,
                "written": self._written,
                "rejected": self._rejected,
                "buffer_capacity": self._buffer.capacity,
            },
        )
