"""Status dan progres sinkronisasi."""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["SyncProgress", "SyncStats"]


@dataclass
class SyncStats:
    """Penghitung sepanjang umur proses."""

    batches_built: int = 0
    batches_acked: int = 0
    batches_failed: int = 0
    batches_quarantined: int = 0
    records_synced: int = 0
    bytes_sent: int = 0
    resumes: int = 0
    duplicate_acks: int = 0
    """Batch yang sudah committed di central lalu dikirim ulang. Bukan error —
    ini justru bukti idempotensi bekerja."""
    attempts_by_transport: dict[str, int] = field(default_factory=dict)

    def record_attempt(self, transport: str) -> None:
        self.attempts_by_transport[transport] = self.attempts_by_transport.get(transport, 0) + 1


@dataclass(frozen=True, slots=True)
class SyncProgress:
    """Gambaran keadaan sekarang, untuk Edge Console dan heartbeat."""

    pending_records: int
    pending_batches: int
    oldest_pending_age_seconds: float | None
    """Indikator paling jujur untuk "seberapa tertinggal kapal ini"."""
    active_transport: str | None
    last_success_us: int | None
    last_error: str | None
    quarantined_batches: int
    """Batch yang gagal permanen. Datanya masih ada — perlu perhatian operator."""
    acked_records: int = 0
    """Record yang sudah dikonfirmasi pusat dan masih disimpan di kapal. Dipangkas
    setelah `acked_grace_days`, jadi ini angka jendela waktu, bukan total seumur hidup."""
    quarantined_records: int = 0
    """Record di dalam batch yang dikarantina. Belum ada di pusat."""
    acked_bytes: int = 0
    """Perkiraan ukuran data yang sudah terkirim (gzip JSON di outbox). Sebanding
    dengan yang lewat jaringan, tetapi bukan hitungan byte di kabel."""
    unsent_bytes: int = 0
    """Perkiraan ukuran data yang belum ada di pusat, termasuk yang tertahan."""
    quarantined_bytes: int = 0
    """Bagian dari `unsent_bytes` yang berada di batch dikarantina."""
