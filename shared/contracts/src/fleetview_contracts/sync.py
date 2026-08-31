"""Model bidang ingest: ACK, sync-state, dan upload session."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from fleetview_contracts.enums import BatchStatus

__all__ = ["Ack", "SyncState", "UploadSession"]


class Ack(BaseModel):
    """Bukti bahwa central sudah menyimpan sebuah batch secara durable.

    Edge menandai baris outbox sebagai acked **hanya** setelah menerima ini.
    Server menyimpan ACK yang diterbitkannya, sehingga ACK yang hilang di jalan
    bisa diputar ulang identik saat edge mengirim ulang — tidak perlu upload ulang.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    batch_id: UUID
    status: BatchStatus
    last_contiguous_sequence: Annotated[int, Field(ge=0)]
    """Watermark resmi server: sampai sequence berapa data sudah lengkap tanpa celah."""
    server_received_at: datetime
    record_count: Annotated[int, Field(ge=0)]
    message: str | None = None

    @field_validator("server_received_at")
    @classmethod
    def _require_tzaware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("server_received_at harus membawa timezone")
        return v


class SyncState(BaseModel):
    """Jawaban server atas pertanyaan "apa yang sudah kamu punya dari kapal ini?".

    Edge menanyakan ini sebelum mengirim, agar bisa melewati batch yang sudah
    committed dan tidak membuang bandwidth untuk data yang sudah sampai.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ship_id: UUID
    last_contiguous_sequence: Annotated[int, Field(ge=0)]
    highest_sequence_seen: Annotated[int, Field(ge=0)]
    """Bila lebih besar dari last_contiguous_sequence, berarti ada celah —
    normal terjadi setelah impor USB berisi data lama."""
    committed_batch_ids: list[UUID] = Field(default_factory=list)
    server_time: datetime
    """Dipakai edge untuk menghitung clock skew-nya sendiri."""

    @field_validator("server_time")
    @classmethod
    def _require_tzaware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("server_time harus membawa timezone")
        return v

    @property
    def has_gap(self) -> bool:
        return self.highest_sequence_seen > self.last_contiguous_sequence


class UploadSession(BaseModel):
    """Sesi upload yang bisa dilanjutkan, untuk batch yang dikirim per potongan."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    session_id: UUID
    batch_id: UUID
    chunk_size: Annotated[int, Field(gt=0)]
    total_chunks: Annotated[int, Field(gt=0)]
    received_chunks: list[int] = Field(default_factory=list)
    """Indeks potongan yang sudah tersimpan di server. Edge mengirim sisanya saja."""
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def _require_tzaware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("expires_at harus membawa timezone")
        return v

    @property
    def missing_chunks(self) -> list[int]:
        received = set(self.received_chunks)
        return [i for i in range(self.total_chunks) if i not in received]
