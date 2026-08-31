"""Batch envelope — unit sinkronisasi dan unit idempotensi."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from fleetview_contracts.enums import Transport
from fleetview_contracts.version import SCHEMA_VERSION

__all__ = ["BatchEnvelope", "BatchPayload", "PayloadEncoding"]

PayloadEncoding = str
CHECKSUM_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


class BatchEnvelope(BaseModel):
    """Metadata satu batch. Dikirim sebagai header sebelum payload-nya.

    `batch_id` dibuat di edge dan menjadi primary key ledger server. Karena itu
    pengiriman ulang menghasilkan konflik primary key, bukan data ganda — inilah
    lapis pertama pengaman idempotensi.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = SCHEMA_VERSION
    batch_id: UUID
    """UUIDv7 — terurut waktu, sehingga index server tetap rapi saat 70 kapal
    mengirim bersamaan."""

    ship_id: UUID
    device_id: UUID

    sequence_start: Annotated[int, Field(ge=0)]
    sequence_end: Annotated[int, Field(ge=0)]
    """Inklusif. Rentang harus berurutan tanpa celah di outbox edge."""

    first_timestamp: datetime
    last_timestamp: datetime
    record_count: Annotated[int, Field(gt=0)]

    payload_checksum: str
    """`sha256:<64 hex>` atas byte payload **terkompresi**."""

    payload_encoding: PayloadEncoding = "gzip+json"
    payload_size: Annotated[int, Field(gt=0)]
    """Ukuran byte setelah kompresi."""

    agent_version: str
    config_version: str
    created_at: datetime
    transport: Transport | None = None
    """Diisi oleh Sync Engine saat pengiriman; None saat batch baru dibentuk."""

    @field_validator("payload_checksum")
    @classmethod
    def _check_checksum(cls, v: str) -> str:
        if not CHECKSUM_PATTERN.match(v):
            raise ValueError(f"payload_checksum harus 'sha256:<64 hex>', dapat {v!r}")
        return v

    @field_validator("first_timestamp", "last_timestamp", "created_at")
    @classmethod
    def _require_tzaware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError(
                "timestamp harus membawa timezone. Datetime naif tidak bisa "
                "dibandingkan lintas kapal dan lintas zona waktu."
            )
        return v

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        if self.sequence_end < self.sequence_start:
            raise ValueError(
                f"sequence_end ({self.sequence_end}) < sequence_start ({self.sequence_start})"
            )
        span = self.sequence_end - self.sequence_start + 1
        if span != self.record_count:
            raise ValueError(
                f"record_count ({self.record_count}) tidak cocok dengan rentang "
                f"sequence ({span}). Rentang harus berurutan tanpa celah."
            )
        if self.last_timestamp < self.first_timestamp:
            raise ValueError("last_timestamp lebih awal dari first_timestamp")
        return self


class BatchPayload(BaseModel):
    """Isi batch setelah didekompresi dan didekode.

    Yang melintasi jaringan adalah bentuk terkompresinya; model ini adalah
    representasi terurainya, dipakai validasi di kedua sisi.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    readings: Annotated[list[object], Field(min_length=1)]
    """Sengaja `list[object]` untuk menghindari impor melingkar dengan reading.py;
    Sync Engine dan modul ingest memvalidasinya sebagai `list[Reading]`."""
