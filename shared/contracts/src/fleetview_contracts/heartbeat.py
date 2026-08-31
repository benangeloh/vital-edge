"""Heartbeat — tanda hidup dari kapal, sekaligus jalur turun dari central.

Heartbeat berfungsi dua arah: edge melaporkan kesehatannya, dan response-nya
membawa waktu server plus versi config yang menunggu. Dengan begitu edge tidak
butuh koneksi masuk sama sekali dan tidak perlu loop polling sendiri.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from fleetview_contracts.enums import Transport

__all__ = ["EdgeHealth", "Heartbeat", "HeartbeatResponse"]


class EdgeHealth(BaseModel):
    """Ringkasan kesehatan Edge Agent, cukup untuk mengisi tampilan armada."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    disk_used_percent: Annotated[float, Field(ge=0, le=100)]
    memory_used_percent: Annotated[float, Field(ge=0, le=100)]
    cpu_used_percent: Annotated[float, Field(ge=0, le=100)]
    uptime_seconds: Annotated[int, Field(ge=0)]
    collector_healthy: bool
    storage_healthy: bool
    protocol_link_healthy: bool
    """Status sambungan ke perangkat lapangan (LP-A104 atau penggantinya)."""
    clock_skew_seconds: float | None = None
    """Selisih jam edge terhadap server pada heartbeat terakhir. Positif = edge
    mendahului. None bila belum pernah tersambung."""


class Heartbeat(BaseModel):
    """Dikirim berkala oleh edge, terlepas dari ada tidaknya data untuk disinkronkan."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ship_id: UUID
    device_id: UUID
    sent_at: datetime
    agent_version: str
    config_version: str
    active_transport: Transport | None = None
    pending_batches: Annotated[int, Field(ge=0)] = 0
    pending_records: Annotated[int, Field(ge=0)] = 0
    oldest_pending_age_seconds: int | None = None
    """Umur data belum terkirim yang paling lama. Ini indikator paling jujur
    untuk "seberapa tertinggal kapal ini", jauh lebih berguna daripada jumlah baris."""
    last_telemetry_at: datetime | None = None
    health: EdgeHealth | None = None

    @field_validator("sent_at")
    @classmethod
    def _require_tzaware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("sent_at harus membawa timezone")
        return v


class HeartbeatResponse(BaseModel):
    """Jawaban central. Membawa waktu server dan perintah yang menunggu."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    server_time: datetime
    config_version: str | None = None
    """Bila berbeda dari milik edge, ada config baru yang perlu diambil."""
    commands: list[str] = Field(default_factory=list)
    """Perintah sederhana, mis. `sync_now`. Sengaja dibiarkan minimal sampai
    ada kebutuhan nyata — jangan dijadikan saluran RPC."""

    @field_validator("server_time")
    @classmethod
    def _require_tzaware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("server_time harus membawa timezone")
        return v
