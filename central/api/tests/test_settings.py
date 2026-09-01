"""Test config Central API."""

from __future__ import annotations

import os

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


def test_cors_default_kosong(monkeypatch: pytest.MonkeyPatch) -> None:
    """Daftar origin yang longgar adalah cara paling mudah membocorkan API internal."""
    assert _default_settings(monkeypatch).cors_origins == []


def _default_settings(monkeypatch: pytest.MonkeyPatch) -> ApiSettings:
    """ApiSettings tanpa pengaruh apa pun dari luar.

    Menguji "nilai default" sambil membaca berkas `.env` pengembang berarti tidak
    menguji default sama sekali — dan `.env` itu justru langkah wajib di
    docs/operations/00-menjalankan.md, sehingga test seperti itu gagal di mesin
    siapa pun yang mengikuti panduannya, tetapi lolos di CI yang tidak punya `.env`.
    """
    for key in list(os.environ):
        if key.startswith("FLEETVIEW_"):
            monkeypatch.delenv(key, raising=False)
    return ApiSettings(_env_file=None)
