"""Model outbox."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from fleetview_contracts import SyncPriority, TelemetryRecord

__all__ = ["BatchRow", "BatchState", "OutboxChunk", "RecordState"]


class RecordState(StrEnum):
    PENDING = "pending"
    """Sudah durable, belum masuk batch mana pun."""
    BATCHED = "batched"
    """Sudah dialokasikan ke sebuah batch yang sedang dikirim."""
    ACKED = "acked"
    """Central sudah mengonfirmasi penyimpanan. Boleh dipangkas setelah masa tenggang."""


class BatchState(StrEnum):
    BUILT = "built"
    UPLOADING = "uploading"
    ACKED = "acked"
    FAILED = "failed"
    """Gagal permanen dan dikarantina. Datanya TIDAK dihapus."""


@dataclass(frozen=True, slots=True)
class OutboxChunk:
    """Satu baris outbox: sekelompok pembacaan berurutan yang disimpan bersama.

    Chunk adalah satuan penyimpanan **dan** satuan state. Sebuah batch selalu
    terdiri dari chunk-chunk utuh — tidak pernah separuh chunk — karena
    `state` melekat pada baris, bukan pada pembacaan di dalamnya. Membatch
    separuh chunk akan menandai seluruh chunk sebagai terkirim padahal
    sebagiannya tidak, yaitu kehilangan data tanpa jejak.
    """

    sequence_start: int
    sequence_end: int
    captured_at: int
    priority: SyncPriority
    records: list[TelemetryRecord]
    state: RecordState
    batch_id: str | None = None

    @property
    def record_count(self) -> int:
        return len(self.records)


@dataclass(frozen=True, slots=True)
class BatchRow:
    batch_id: str
    sequence_start: int
    sequence_end: int
    first_timestamp: int
    last_timestamp: int
    record_count: int
    payload_checksum: str
    schema_version: str
    priority: SyncPriority
    state: BatchState
    attempts: int = 0
    transport: str | None = None
    next_attempt_at: int | None = None
    acked_at: int | None = None
    last_error: str | None = None
