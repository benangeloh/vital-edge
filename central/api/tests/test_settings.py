"""Test config Central API."""

from __future__ import annotations

import pytest

from fleetview_central.settings import ApiSettings
from fleetview_common import ConfigError


def _prod(**over: object) -> ApiSettings:
    base: dict[str, object] = {
        "environment": "production",
        "log_format": "json",
        "jwt_secret": "x" * 32,
        "influx_token": "t",
        "postgres_dsn": "postgresql+asyncpg://u:p@db:5432/fleetview",
    }
    return ApiSettings(**{**base, **over})  # type: ignore[arg-type]


def test_development_dilewati_tanpa_pemeriksaan() -> None:
    ApiSettings(environment="development").verify_production_ready()


def test_config_produksi_lengkap_lolos() -> None:
    _prod().verify_production_ready()


def test_jwt_secret_kosong_ditolak() -> None:
    with pytest.raises(ConfigError, match="jwt_secret"):
        _prod(jwt_secret="").verify_production_ready()


def test_jwt_secret_pendek_ditolak() -> None:
    with pytest.raises(ConfigError, match="32 karakter"):
        _prod(jwt_secret="pendek").verify_production_ready()


def test_postgres_localhost_di_produksi_ditolak() -> None:
    """Hampir selalu berarti .env produksi lupa diisi."""
    with pytest.raises(ConfigError, match="localhost"):
        _prod(postgres_dsn="postgresql+asyncpg://u:p@localhost:5432/f").verify_production_ready()


def test_cors_default_kosong() -> None:
    """Daftar origin yang longgar adalah cara paling mudah membocorkan API internal."""
    assert ApiSettings().cors_origins == []
