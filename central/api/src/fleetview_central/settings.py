"""Konfigurasi Central Platform.

Berbeda dari edge, central berjalan di lingkungan yang kita kendalikan, jadi
konfigurasinya murni environment variable — tidak ada berkas YAML. Rahasia
datang dari environment atau secret manager, tidak pernah dari berkas di repo.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from fleetview_common import ConfigError

__all__ = ["ApiSettings", "load_settings"]


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FLEETVIEW_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Literal["development", "production"] = "development"
    log_level: str = "INFO"
    log_format: Literal["console", "json"] = "json"

    host: str = "0.0.0.0"
    port: int = Field(default=8000, gt=0, lt=65536)

    postgres_dsn: str = "postgresql+asyncpg://fleetview:fleetview@localhost:5432/fleetview"

    influx_url: str = "http://localhost:8086"
    influx_org: str = "fleetview"
    influx_bucket: str = "telemetry"
    influx_token: str = ""

    jwt_secret: str = ""
    """Kosong hanya boleh saat pengembangan. Diverifikasi ada di produksi."""
    jwt_ttl_seconds: int = Field(default=3600, gt=0)

    cors_origins: list[str] = Field(default_factory=list)
    """Origin dashboard. Sengaja kosong secara default — daftar yang terlalu
    longgar adalah cara paling mudah membocorkan API internal."""

    @field_validator("log_level")
    @classmethod
    def _check_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in allowed:
            raise ValueError(f"log_level harus salah satu dari {sorted(allowed)}, dapat {v!r}")
        return upper

    def verify_production_ready(self) -> None:
        """Gagal cepat bila config produksi punya lubang."""
        if self.environment != "production":
            return

        problems: list[str] = []
        if not self.jwt_secret:
            problems.append("jwt_secret kosong")
        elif len(self.jwt_secret) < 32:
            problems.append("jwt_secret lebih pendek dari 32 karakter")
        if not self.influx_token:
            problems.append("influx_token kosong")
        if "localhost" in self.postgres_dsn:
            problems.append("postgres_dsn masih menunjuk localhost")
        if self.log_format != "json":
            problems.append("log_format harus 'json' di produksi")
        if problems:
            raise ConfigError(
                "config produksi tidak lengkap: " + "; ".join(problems),
                code="config.production_incomplete",
                details={"problems": problems},
            )


def load_settings() -> ApiSettings:
    try:
        return ApiSettings()
    except Exception as exc:
        raise ConfigError(f"config tidak valid: {exc}", code="config.invalid") from exc
