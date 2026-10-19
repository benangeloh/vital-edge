"""Test kesehatan penyimpanan host Central.

Unit test murni: tidak menyentuh Postgres atau InfluxDB sungguhan. Fixture
integration di conftest.py menjalankan `drop_all` pada database dev, jadi test
yang cukup dengan dependency palsu sengaja tidak memakainya.
"""

from __future__ import annotations

import shutil
from collections import namedtuple
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError as PydanticValidationError

from fleetview_central.main import create_app
from fleetview_central.modules.ops.storage import StorageLevel, classify, read_disk_usage
from fleetview_central.platform.deps import get_db_session, get_influx
from fleetview_central.settings import ApiSettings

_Usage = namedtuple("_Usage", "total used free")


class TestKlasifikasi:
    @pytest.mark.parametrize(
        ("persen", "level"),
        [
            (0.0, StorageLevel.OK),
            (79.9, StorageLevel.OK),
            (80.0, StorageLevel.WARNING),
            (89.9, StorageLevel.WARNING),
            (90.0, StorageLevel.CRITICAL),
            (100.0, StorageLevel.CRITICAL),
        ],
    )
    def test_ambang_warn_80_critical_90(self, persen: float, level: StorageLevel) -> None:
        assert classify(persen, warn=80.0, critical=90.0) is level


class TestBacaDisk:
    def test_membaca_partisi_sungguhan(self, tmp_path: Path) -> None:
        usage = read_disk_usage(str(tmp_path), warn=80.0, critical=90.0)

        assert usage is not None
        assert usage.total_bytes > 0
        assert usage.used_bytes + usage.free_bytes <= usage.total_bytes + 1
        assert 0.0 <= usage.used_percent <= 100.0
        assert usage.path == str(tmp_path)

    def test_persen_dihitung_dari_total(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(shutil, "disk_usage", lambda _p: _Usage(1000, 850, 150))

        usage = read_disk_usage(str(tmp_path), warn=80.0, critical=90.0)

        assert usage is not None
        assert usage.used_percent == pytest.approx(85.0)
        assert usage.free_bytes == 150
        assert usage.level is StorageLevel.WARNING

    def test_path_tidak_ada_mengembalikan_none(self, tmp_path: Path) -> None:
        """Panel kesehatan tidak boleh gagal total karena satu path salah config."""
        assert read_disk_usage(str(tmp_path / "tidak-ada"), warn=80.0, critical=90.0) is None

    def test_total_nol_mengembalikan_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(shutil, "disk_usage", lambda _p: _Usage(0, 0, 0))
        assert read_disk_usage(str(tmp_path), warn=80.0, critical=90.0) is None


class TestSettingsAmbang:
    def test_default_80_dan_90(self) -> None:
        s = ApiSettings(environment="development")
        assert (s.disk_warn_percent, s.disk_critical_percent) == (80.0, 90.0)

    def test_warn_tidak_boleh_melebihi_critical(self) -> None:
        with pytest.raises(PydanticValidationError, match="disk_warn_percent"):
            ApiSettings(
                environment="development", disk_warn_percent=95.0, disk_critical_percent=90.0
            )


class _FakeResult:
    def __init__(self, value: int) -> None:
        self._value = value

    def scalar_one(self) -> int:
        return self._value


class _FakeSession:
    def __init__(self, *, down: bool = False, db_bytes: int = 4096) -> None:
        self._down = down
        self._db_bytes = db_bytes

    async def execute(self, _stmt: Any) -> _FakeResult:
        if self._down:
            raise RuntimeError("postgres mati")
        return _FakeResult(self._db_bytes)


class _FakeInflux:
    def __init__(self, *, healthy: bool = True) -> None:
        self._healthy = healthy

    async def healthy(self) -> bool:
        return self._healthy


def _client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    persen: float = 50.0,
    pg_down: bool = False,
    influx_up: bool = True,
    **settings_over: Any,
) -> TestClient:
    total = 1000
    used = int(total * persen / 100)
    monkeypatch.setattr(shutil, "disk_usage", lambda _p: _Usage(total, used, total - used))

    app = create_app(ApiSettings(environment="development", log_format="console", **settings_over))
    app.dependency_overrides[get_db_session] = lambda: _FakeSession(down=pg_down)
    app.dependency_overrides[get_influx] = lambda: _FakeInflux(healthy=influx_up)
    return TestClient(app)


class TestEndpointHealth:
    def test_menyertakan_blok_storage(self, monkeypatch: pytest.MonkeyPatch) -> None:
        data = _client(monkeypatch, persen=42.0).get("/api/v1/health").json()["data"]

        assert data["status"] == "healthy"
        assert data["checks"] == {"postgres": "ok", "influxdb": "ok"}
        storage = data["storage"]
        assert storage["disk"]["used_percent"] == pytest.approx(42.0)
        assert storage["disk"]["level"] == "ok"
        assert storage["disk"]["free_bytes"] == 580
        assert storage["postgres_bytes"] == 4096

    def test_disk_warning_tidak_membuat_status_degraded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Peringatan perlu terlihat, tetapi bukan alasan menyatakan platform rusak."""
        data = _client(monkeypatch, persen=85.0).get("/api/v1/health").json()["data"]

        assert data["status"] == "healthy"
        assert data["storage"]["disk"]["level"] == "warning"

    def test_disk_kritis_membuat_status_degraded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        data = _client(monkeypatch, persen=93.0).get("/api/v1/health").json()["data"]

        assert data["status"] == "degraded"
        assert data["storage"]["disk"]["level"] == "critical"

    def test_postgres_mati_tetap_melaporkan_disk(self, monkeypatch: pytest.MonkeyPatch) -> None:
        data = _client(monkeypatch, pg_down=True).get("/api/v1/health").json()["data"]

        assert data["status"] == "degraded"
        assert data["checks"]["postgres"].startswith("gagal")
        assert data["storage"]["postgres_bytes"] is None
        assert data["storage"]["disk"]["level"] == "ok"

    def test_path_disk_salah_tidak_menjatuhkan_endpoint(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        client = _client(monkeypatch, health_disk_path=str(tmp_path / "tidak-ada"))
        # disk_usage dipatch di _client; path yang tidak ada ditolak sebelum dipanggil.
        data = client.get("/api/v1/health").json()["data"]

        assert data["status"] == "healthy"
        assert data["storage"]["disk"] is None
