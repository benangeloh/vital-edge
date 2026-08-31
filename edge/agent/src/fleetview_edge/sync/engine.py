"""SyncEngine — satu loop untuk semua transport.

Aturan yang menopang seluruh Phase 4:

**Outbox adalah source of truth.** Transport hanya mengantar. Satu-satunya hal
yang memindahkan batch keluar dari pending adalah ACK yang sah dari central —
bukan berkas yang tertulis, bukan HTTP 200 tanpa ACK, bukan percobaan yang
"kelihatannya berhasil".

**Data lokal tidak pernah dihapus sebelum ACK.** Pemangkasan hanya menyentuh
baris yang sudah acked, dan itu pun setelah masa tenggang.

**Percobaan ulang itu gratis.** `batch_id` dibuat di kapal dan menjadi primary
key ledger di central, sehingga kiriman ulang memutar ulang ACK yang tersimpan
alih-alih menggandakan data.
"""

from __future__ import annotations

import asyncio
import contextlib

from fleetview_common import FleetViewError, get_logger, log_context, now_micros
from fleetview_edge.collector.supervisor import BackoffPolicy
from fleetview_edge.outbox import OutboxStore
from fleetview_edge.sync.base import (
    TransportAdapter,
    TransportRejectedError,
    TransportResult,
)
from fleetview_edge.sync.batcher import Batcher
from fleetview_edge.sync.manager import NetworkManager, TransportSlot
from fleetview_edge.sync.status import SyncProgress, SyncStats

__all__ = ["SyncEngine"]

log = get_logger(__name__)


class SyncEngine:
    """Menyetor data dari outbox ke central lewat transport apa pun yang ada.

    Args:
        store: outbox, source of truth.
        batcher: pembentuk batch.
        manager: pemilih transport.
        backoff: kebijakan percobaan ulang.
        max_attempts: setelah sekian kegagalan berturut-turut, batch dikarantina.
            Datanya **tidak** dihapus — hanya berhenti dicoba supaya tidak
            menyumbat antrean.
        acked_grace_seconds: berapa lama baris yang sudah acked disimpan sebelum
            dipangkas. Menyisakan jendela replay lokal seandainya central pernah
            kehilangan data.
    """

    def __init__(
        self,
        *,
        store: OutboxStore,
        batcher: Batcher,
        manager: NetworkManager,
        backoff: BackoffPolicy | None = None,
        max_attempts: int = 10,
        acked_grace_seconds: int = 7 * 86_400,
        interval_seconds: float = 10.0,
    ) -> None:
        self._store = store
        self._batcher = batcher
        self._manager = manager
        self._backoff = backoff or BackoffPolicy(initial_seconds=5.0, max_seconds=1800.0)
        self._max_attempts = max_attempts
        self._grace_us = acked_grace_seconds * 1_000_000
        self._interval = interval_seconds

        self._stats = SyncStats()
        self._stopping = asyncio.Event()
        self._active_transport: str | None = None
        self._last_success_us: int | None = None
        self._last_error: str | None = None

    @property
    def stats(self) -> SyncStats:
        return self._stats

    def stop(self) -> None:
        self._stopping.set()

    # -- loop ---------------------------------------------------------------

    async def run(self) -> None:
        """Jalankan loop sinkronisasi sampai stop() dipanggil.

        Loop tidak pernah berhenti sendiri karena kegagalan. Kapal berlayar
        berbulan-bulan tanpa ada yang bisa menyalakan ulang prosesnya.
        """
        log.info("sync.engine_started", interval_seconds=self._interval)
        try:
            while not self._stopping.is_set():
                try:
                    await self.sync_once()
                except Exception:
                    # Loop sync tidak boleh mati. Kegagalan tak terduga dicatat
                    # lalu dicoba lagi pada putaran berikutnya.
                    log.exception("sync.cycle_failed")
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._stopping.wait(), timeout=self._interval)
        finally:
            log.info(
                "sync.engine_stopped",
                acked=self._stats.batches_acked,
                records=self._stats.records_synced,
            )

    async def sync_once(self) -> int:
        """Satu putaran: pilih transport, bentuk batch, kirim. Kembalikan jumlah ACK."""
        slot = await self._manager.select()
        if slot is None:
            self._active_transport = None
            return 0

        self._active_transport = slot.adapter.name
        acked = 0

        # Kirim ulang batch yang sudah dibentuk sebelumnya lebih dulu. Yang
        # paling lama harus keluar duluan supaya watermark contiguous di server
        # maju dengan stabil dan angka "pending" punya arti.
        for row in self._store.batches_ready(limit=5):
            if row.attempts >= self._max_attempts:
                self._quarantine(row.batch_id, row.last_error or "melebihi batas percobaan")
                continue
            built = self._batcher.rebuild(row.batch_id)
            if built is None:
                self._store.release_batch(row.batch_id)
                continue
            if await self._deliver(slot, built.envelope, built.payload):
                acked += 1

        # Lalu bentuk batch baru, mulai dari prioritas tertinggi.
        while not self._stopping.is_set():
            built = self._batcher.build_next(max_priority=slot.effective_max_priority)
            if built is None:
                break
            self._stats.batches_built += 1
            if await self._deliver(slot, built.envelope, built.payload):
                acked += 1
            else:
                break  # transport bermasalah; jangan menumpuk batch baru

        self._prune()
        return acked

    # -- pengiriman ---------------------------------------------------------

    async def _deliver(self, slot: TransportSlot, envelope, payload: bytes) -> bool:  # type: ignore[no-untyped-def]
        batch_id = str(envelope.batch_id)
        transport: TransportAdapter = slot.adapter

        with log_context(batch_id=batch_id, transport=transport.name):
            self._stats.record_attempt(transport.name)
            try:
                result: TransportResult = await transport.send(envelope, payload)
            except TransportRejectedError as exc:
                # Ditolak permanen. Dikarantina, TIDAK dihapus — operator harus
                # bisa melihat data yang bermasalah, bukan kehilangannya diam-diam.
                self._last_error = exc.message
                self._stats.batches_failed += 1
                self._quarantine(batch_id, exc.message)
                log.error(
                    "sync.batch_rejected",
                    code=exc.code,
                    error=exc.message,
                    note="dikarantina; data tetap tersimpan lokal",
                    details=exc.details,
                )
                return False
            except FleetViewError as exc:
                self._last_error = exc.message
                self._stats.batches_failed += 1
                self._schedule_retry(batch_id, transport.name, exc.message)
                log.warning("sync.batch_deferred", code=exc.code, error=exc.message)
                return False

            slot.record_usage(result.bytes_sent)
            self._stats.bytes_sent += result.bytes_sent
            if result.resumed_from_chunk:
                self._stats.resumes += 1

            if result.ack is None:
                # Export USB: berkas tertulis, tetapi tidak ada yang
                # mengonfirmasi central menerimanya. Batch TETAP pending.
                log.info(
                    "sync.batch_delivered_unacked",
                    detail=result.detail,
                    note="tetap pending sampai ACK sungguhan diterima",
                )
                self._schedule_retry(batch_id, transport.name, None, immediate=True)
                return False

            self._store.mark_acked(batch_id, transport=transport.name)
            self._stats.batches_acked += 1
            self._stats.records_synced += envelope.record_count
            self._last_success_us = now_micros()
            self._last_error = None
            if result.bytes_sent == 0:
                self._stats.duplicate_acks += 1

            log.info(
                "sync.batch_acked",
                records=envelope.record_count,
                bytes_sent=result.bytes_sent,
                resumed_from=result.resumed_from_chunk,
                last_contiguous=result.ack.last_contiguous_sequence,
            )
            return True

    def _schedule_retry(
        self, batch_id: str, transport: str, error: str | None, *, immediate: bool = False
    ) -> None:
        row = self._store.batch(batch_id)
        attempts = (row.attempts if row else 0) + 1
        # BackoffPolicy sudah menerapkan jitter-nya sendiri — itulah yang
        # menyebarkan percobaan 70 kapal yang tersambung kembali bersamaan.
        # Menambahkan jitter kedua di sini membuat setelan policy tidak berarti
        # dan besaran delay-nya tidak bisa diprediksi.
        delay = 0.0 if immediate else self._backoff.delay_for(attempts)
        next_at = now_micros() + int(delay * 1_000_000)
        self._store.mark_attempt(
            batch_id, transport=transport, next_attempt_at=next_at, error=error
        )

    def _quarantine(self, batch_id: str, error: str) -> None:
        self._store.mark_failed(batch_id, error=error)
        self._stats.batches_quarantined += 1

    def _prune(self) -> None:
        """Pangkas baris yang sudah acked dan melewati masa tenggang.

        Hanya yang sudah acked. Baris pending tidak pernah dipangkas, berapa pun
        tekanan disk.
        """
        removed = self._store.prune_acked(older_than_us=now_micros() - self._grace_us)
        if removed:
            log.info("sync.pruned_acked_records", removed=removed)

    # -- ekspor manual ------------------------------------------------------

    async def export_to(self, slot: TransportSlot, *, max_batches: int = 100) -> int:
        """Ekspor data pending ke transport berkas. Kembalikan jumlah batch.

        Dipanggil operator lewat Edge Console. Sengaja terpisah dari loop
        otomatis: menulis ke flashdisk adalah tindakan yang diminta manusia,
        bukan yang terjadi diam-diam karena jaringan sedang mati.

        **Tidak menghapus apa pun.** Batch tetap pending sampai ACK sungguhan.
        """
        exported = 0
        for _ in range(max_batches):
            built = self._batcher.build_next()
            if built is None:
                break
            try:
                result = await slot.adapter.send(built.envelope, built.payload)
            except FleetViewError as exc:
                self._store.release_batch(str(built.envelope.batch_id))
                log.warning("sync.export_failed", error=exc.message, code=exc.code)
                break
            slot.record_usage(result.bytes_sent)
            self._schedule_retry(
                str(built.envelope.batch_id), slot.adapter.name, None, immediate=True
            )
            exported += 1

        log.info(
            "sync.export_completed",
            batches=exported,
            note="data tetap pending; export bukan pengganti ACK",
        )
        return exported

    # -- status -------------------------------------------------------------

    def progress(self) -> SyncProgress:
        stats = self._store.stats()
        return SyncProgress(
            pending_records=stats.get("records_pending", 0),
            pending_batches=stats.get("batches_built", 0) + stats.get("batches_uploading", 0),
            oldest_pending_age_seconds=self._store.oldest_pending_age_seconds(),
            active_transport=self._active_transport,
            last_success_us=self._last_success_us,
            last_error=self._last_error,
            quarantined_batches=stats.get("batches_failed", 0),
        )

    async def snapshot(self) -> dict[str, object]:
        p = self.progress()
        return {
            "pending_records": p.pending_records,
            "pending_batches": p.pending_batches,
            "oldest_pending_age_seconds": p.oldest_pending_age_seconds,
            "active_transport": p.active_transport,
            "last_success_us": p.last_success_us,
            "last_error": p.last_error,
            "quarantined_batches": p.quarantined_batches,
            "batches_acked": self._stats.batches_acked,
            "records_synced": self._stats.records_synced,
            "bytes_sent": self._stats.bytes_sent,
            "resumes": self._stats.resumes,
            "duplicate_acks": self._stats.duplicate_acks,
            "transports": await self._manager.snapshot(),
        }
