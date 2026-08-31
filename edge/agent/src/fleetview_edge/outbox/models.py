"""Model outbox."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from fleetview_contracts import SyncPriority, TelemetryRecord

__all__ = ["BatchRow", "BatchState", "OutboxRow", "RecordState"]


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
class OutboxRow:
    sequence: int
    captured_at: int
    priority: SyncPriority
    record: TelemetryRecord
    state: RecordState
    batch_id: str | None = None


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
