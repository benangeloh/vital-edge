"""Manifest export USB/HDD.

USB adalah transport, bukan database. Berkas batch di dalam folder export
identik byte-per-byte dengan yang akan dikirim lewat HTTP, sehingga hanya ada
satu jalur ingest yang perlu diuji dan diamankan.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = ["ExportManifest", "ExportedBatch"]


class ExportedBatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    batch_id: UUID
    filename: str
    sequence_start: Annotated[int, Field(ge=0)]
    sequence_end: Annotated[int, Field(ge=0)]
    record_count: Annotated[int, Field(gt=0)]
    payload_checksum: str
    payload_size: Annotated[int, Field(gt=0)]


class ExportManifest(BaseModel):
    """Ditulis di akar folder export. Diverifikasi tool usb-import sebelum ingest."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    export_id: UUID
    ship_id: UUID
    device_id: UUID
    created_at: datetime
    agent_version: str
    schema_version: str
    batches: Annotated[list[ExportedBatch], Field(min_length=1)]
    export_checksum: str
    """sha256 atas manifest yang sudah dinormalisasi, tanpa field ini sendiri.
    Mendeteksi media rusak sebelum kita mempercayai satu byte pun isinya."""

    @field_validator("created_at")
    @classmethod
    def _require_tzaware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("created_at harus membawa timezone")
        return v

    @property
    def total_records(self) -> int:
        return sum(b.record_count for b in self.batches)
