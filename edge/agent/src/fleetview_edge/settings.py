"""Konfigurasi Edge Agent.

Config disusun berlapis, yang belakangan menimpa yang sebelumnya:

  1. default di dalam kode (di file ini)
  2. berkas YAML  (`--config` atau `FLEETVIEW_EDGE_CONFIG`)
  3. environment variable (berawalan `FLEETVIEW_`)

Lapisan keempat — config yang didorong dari central dan berversi — menyusul di
Phase 5. Itulah alasan `config_version` sudah ada sekarang: ia ikut di setiap
heartbeat dan setiap batch, sehingga saat suatu kapal berperilaku aneh, kita bisa
tahu persis konfigurasi mana yang sedang dijalankannya.

Satu build Edge Agent yang sama dideploy ke 70+ kapal. **Yang membedakan hanya
berkas config ini.** Karena itu identitas kapal wajib diisi dan tidak punya
default — agent menolak start daripada berjalan dengan identitas salah dan
mengirim data atas nama kapal lain.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from fleetview_common import ConfigError

__all__ = ["EdgeSettings", "load_settings"]


class ShipIdentity(BaseModel):
    """Identitas kapal. Wajib, tanpa default."""

    model_config = ConfigDict(extra="forbid")

    ship_id: UUID
    ship_name: str = Field(min_length=1, max_length=128)
    device_id: UUID


class StorageSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data_dir: Path = Path("/var/lib/fleetview")
    outbox_path: Path = Path("/var/lib/fleetview/outbox.db")

    influx_url: str = "http://127.0.0.1:8086"
    influx_org: str = "fleetview"
    influx_bucket: str = "telemetry"
    influx_token: str = ""
    """Kosong hanya boleh saat pengembangan. Diverifikasi ada di produksi."""

    retention_days: int = Field(default=90, ge=1)
    acked_grace_days: int = Field(default=7, ge=0)
    """Berapa lama baris yang sudah acked tetap disimpan. Menyisakan jendela
    replay lokal seandainya central pernah kehilangan data."""

    disk_warn_percent: float = Field(default=80.0, ge=0, le=100)
    disk_critical_percent: float = Field(default=90.0, ge=0, le=100)


class SyncSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    central_url: str = ""
    batch_max_records: int = Field(default=500, gt=0)
    batch_max_bytes: int = Field(default=1_048_576, gt=0)
    batch_max_age_seconds: int = Field(default=60, gt=0)
    heartbeat_interval_seconds: int = Field(default=60, gt=0)
    cellular_monthly_budget_mb: int = Field(default=500, ge=0)


class ConsoleSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    host: str = "127.0.0.1"
    """Default hanya localhost. Ubah ke 0.0.0.0 secara sadar bila perlu diakses
    dari LAN kapal — Console tidak boleh menghadap internet."""
    port: int = Field(default=8080, gt=0, lt=65536)


class EdgeSettings(BaseSettings):
    """Konfigurasi lengkap Edge Agent."""

    model_config = SettingsConfigDict(
        env_prefix="FLEETVIEW_",
        env_nested_delimiter="__",
        extra="forbid",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Urutan prioritas: environment > berkas YAML > default di kode.

        Urutan bawaan pydantic-settings menempatkan argumen init di atas
        environment. Di sini YAML dimuat sebagai argumen init, jadi urutan
        bawaannya akan membuat berkas mengalahkan environment — kebalikan dari
        yang dijanjikan docstring modul ini, dan kebalikan dari yang dibutuhkan
        operasional: rahasia dan penyesuaian per-kapal diberikan lewat env
        justru supaya bisa menimpa berkas config yang sudah ter-provision.
        """
        return (env_settings, dotenv_settings, init_settings, file_secret_settings)

    environment: Literal["development", "production"] = "development"
    log_level: str = "INFO"
    log_format: Literal["console", "json"] = "json"
    config_version: str = "local"

    ship: ShipIdentity
    storage: StorageSettings = Field(default_factory=StorageSettings)
    sync: SyncSettings = Field(default_factory=SyncSettings)
    console: ConsoleSettings = Field(default_factory=ConsoleSettings)

    @field_validator("log_level")
    @classmethod
    def _check_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in allowed:
            raise ValueError(f"log_level harus salah satu dari {sorted(allowed)}, dapat {v!r}")
        return upper

    def verify_production_ready(self) -> None:
        """Gagal cepat bila config produksi punya lubang.

        Dipanggil saat startup. Lebih baik agent menolak jalan daripada berjalan
        24/7 di kapal terpencil dengan token kosong dan diam-diam tidak pernah
        berhasil sinkron.
        """
        if self.environment != "production":
            return

        problems: list[str] = []
        if not self.storage.influx_token:
            problems.append("storage.influx_token kosong")
        if not self.sync.central_url:
            problems.append("sync.central_url kosong")
        if self.log_format != "json":
            problems.append("log_format harus 'json' di produksi supaya log bisa diagregasi")
        if self.console.host == "0.0.0.0":
            problems.append(
                "console.host adalah 0.0.0.0 — Edge Console tidak boleh mendengarkan "
                "di semua antarmuka tanpa keputusan sadar"
            )
        if problems:
            raise ConfigError(
                "config produksi tidak lengkap: " + "; ".join(problems),
                code="config.production_incomplete",
                details={"problems": problems},
            )


def load_settings(config_path: Path | None = None) -> EdgeSettings:
    """Muat config dari berkas YAML dan environment variable.

    Args:
        config_path: berkas YAML opsional. Nilainya jadi lapisan dasar;
            environment variable tetap menimpanya.

    Raises:
        ConfigError: berkas tidak ada, YAML rusak, atau config tidak valid.
    """
    base: dict[str, Any] = {}

    if config_path is not None:
        if not config_path.is_file():
            raise ConfigError(
                f"berkas config tidak ditemukan: {config_path}",
                code="config.file_missing",
                details={"path": str(config_path)},
            )
        try:
            loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ConfigError(
                f"YAML rusak di {config_path}: {exc}",
                code="config.yaml_invalid",
                details={"path": str(config_path)},
            ) from exc
        if loaded is not None:
            if not isinstance(loaded, dict):
                raise ConfigError(
                    f"config harus berupa mapping, dapat {type(loaded).__name__}",
                    code="config.yaml_invalid",
                    details={"path": str(config_path)},
                )
            base = loaded

    try:
        return EdgeSettings(**base)
    except Exception as exc:  # pydantic ValidationError
        raise ConfigError(
            f"config tidak valid: {exc}",
            code="config.invalid",
            details={"path": str(config_path) if config_path else None},
        ) from exc
