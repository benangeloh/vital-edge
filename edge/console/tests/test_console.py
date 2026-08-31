"""Test Edge Console."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fleetview_common import StorageError, ValidationError
from fleetview_console import create_console_app


@pytest.fixture
def client() -> TestClient:
    app = create_console_app(
        ship_name="KM Uji Coba", ship_id="kapal-1", agent_version="0.1.0", environment="development"
    )
    return TestClient(app)


def test_health_menjawab_dengan_envelope(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["data"]["status"] == "alive"
    assert body["data"]["ship_name"] == "KM Uji Coba"


def test_halaman_index_ter_render(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "KM Uji Coba" in r.text


def test_css_tersaji(client: TestClient) -> None:
    r = client.get("/static/console.css")
    assert r.status_code == 200


def test_openapi_dimatikan(client: TestClient) -> None:
    """Console dipakai operator lewat browser, bukan lewat API explorer."""
    assert client.get("/docs").status_code == 404


def _client_with_error(exc: Exception) -> TestClient:
    app: FastAPI = create_console_app(
        ship_name="K", ship_id="i", agent_version="0", environment="development"
    )

    @app.get("/boom")
    async def boom() -> None:
        raise exc

    return TestClient(app, raise_server_exceptions=False)


def test_error_retryable_jadi_503() -> None:
    """Klien harus tahu boleh mencoba lagi."""
    r = _client_with_error(StorageError("influx mati")).get("/boom")
    assert r.status_code == 503
    assert r.json()["error"]["retryable"] is True


def test_error_tidak_retryable_jadi_400() -> None:
    """Klien harus tahu percuma mengulang."""
    r = _client_with_error(ValidationError("payload rusak")).get("/boom")
    assert r.status_code == 400
    assert r.json()["error"]["retryable"] is False
