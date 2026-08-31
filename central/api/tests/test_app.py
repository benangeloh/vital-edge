"""Test rangka Central API."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from fleetview_central.main import create_app
from fleetview_central.settings import ApiSettings
from fleetview_common import AuthError, ConfigError, StorageError, ValidationError


@pytest.fixture
def settings() -> ApiSettings:
    return ApiSettings(environment="development", log_format="console")


@pytest.fixture
def client(settings: ApiSettings) -> TestClient:
    return TestClient(create_app(settings))


def test_health_menjawab_dengan_envelope(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {
        "ok": True,
        "data": {"status": "alive", "version": "0.1.0"},
        "error": None,
        "meta": {},
    }


def test_ready_menjawab(client: TestClient) -> None:
    assert client.get("/api/ready").json()["data"]["status"] == "ready"


def test_docs_terbuka_di_development(client: TestClient) -> None:
    assert client.get("/docs").status_code == 200


def test_docs_tertutup_di_produksi() -> None:
    """Skema API internal tidak perlu dipublikasikan ke internet."""
    s = ApiSettings(
        environment="production",
        log_format="json",
        jwt_secret="x" * 32,
        influx_token="t",
        postgres_dsn="postgresql+asyncpg://u:p@db:5432/fleetview",
    )
    assert TestClient(create_app(s)).get("/docs").status_code == 404


def test_endpoint_tak_dikenal_menjawab_404(client: TestClient) -> None:
    assert client.get("/api/tidak-ada").status_code == 404


class TestPemetaanError:
    """Pemetaan ini mengikuti kontrak retry di 04-api-boundaries.md dan dipatuhi
    Sync Engine secara harfiah — salah memetakan berarti batch rusak permanen
    diulang selamanya, atau kegagalan sementara dianggap fatal."""

    def _client(self, exc: Exception, settings: ApiSettings) -> TestClient:
        app = create_app(settings)

        @app.get("/boom")
        async def boom() -> None:
            raise exc

        return TestClient(app, raise_server_exceptions=False)

    def test_auth_jadi_401(self, settings: ApiSettings) -> None:
        assert self._client(AuthError("token buruk"), settings).get("/boom").status_code == 401

    def test_config_jadi_500(self, settings: ApiSettings) -> None:
        assert self._client(ConfigError("rusak"), settings).get("/boom").status_code == 500

    def test_retryable_jadi_503(self, settings: ApiSettings) -> None:
        r = self._client(StorageError("db mati"), settings).get("/boom")
        assert r.status_code == 503
        assert r.json()["error"]["retryable"] is True

    def test_tidak_retryable_jadi_400(self, settings: ApiSettings) -> None:
        assert self._client(ValidationError("rusak"), settings).get("/boom").status_code == 400

    def test_exception_tak_terduga_tidak_membocorkan_detail(self, settings: ApiSettings) -> None:
        """Pesan exception internal sering memuat DSN, path, atau potongan query."""
        r = self._client(RuntimeError("dsn=postgres://user:rahasia@host"), settings).get("/boom")
        assert r.status_code == 500
        assert "rahasia" not in r.text
        assert r.json()["error"]["code"] == "internal.unexpected"


class TestHandlerTahanTabrakanKwarg:
    """Regresi: `**exc.details` dulu bertabrakan dengan kwarg eksplisit logger.

    Error ingest membawa `details={"status": ...}`, dan itu membuat exception
    handler sendiri crash — 400 yang bersih berubah menjadi 500 beserta stack
    trace. Hanya muncul saat server sungguhan dijalankan, bukan di unit test.
    """

    def _client(self, exc: Exception, settings: ApiSettings) -> TestClient:
        app = create_app(settings)

        @app.get("/boom")
        async def boom() -> None:
            raise exc

        return TestClient(app, raise_server_exceptions=False)

    @pytest.mark.parametrize(
        "details",
        [
            {"status": 422},
            {"code": "lain"},
            {"message": "lain"},
            {"retryable": True},
            {"status": 1, "code": "x", "message": "y", "retryable": False},
        ],
    )
    def test_details_bentrok_tetap_menghasilkan_400(
        self, details: dict[str, object], settings: ApiSettings
    ) -> None:
        exc = ValidationError("rusak", details=details)
        r = self._client(exc, settings).get("/boom")
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "validation.failed"
