"""Test perakitan Edge Agent.

Titik perakitan ini yang menentukan apakah agent menyala atau menolak jalan di
kapal terpencil, jadi ia perlu test tersendiri — bukan hanya lewat komponennya.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fleetview_common import ConfigError
from fleetview_edge.app import EdgeAgent
from fleetview_edge.settings import EdgeSettings
from fleetview_edge.version import AGENT_VERSION

_SHIP = {
    "ship_id": "11111111-1111-1111-1111-111111111111",
    "ship_name": "KM Uji Coba",
    "device_id": "22222222-2222-2222-2222-222222222222",
}


def _settings(**over: object) -> EdgeSettings:
    return EdgeSettings(ship=_SHIP, **over)  # type: ignore[arg-type]


def test_setup_memasang_logging_dan_mencatat_identitas(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Baris identitas ini yang dibaca teknisi lebih dulu saat troubleshooting."""
    EdgeAgent(_settings(log_format="json")).setup()

    line = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert line["event"] == "edge_agent.configured"
    assert line["ship_name"] == "KM Uji Coba"
    assert line["service"] == "edge-agent"
    assert line["version"] == AGENT_VERSION


def test_setup_gagal_cepat_saat_config_produksi_tidak_lengkap() -> None:
    """Lebih baik menolak start daripada berjalan 24/7 dan diam-diam tidak
    pernah berhasil sinkron."""
    agent = EdgeAgent(_settings(environment="production", log_format="json"))
    with pytest.raises(ConfigError):
        agent.setup()


def test_build_console_menghasilkan_app_yang_bisa_melayani() -> None:
    agent = EdgeAgent(_settings(log_format="console"))
    agent.setup()

    app = agent.build_console()
    assert isinstance(app, FastAPI)

    body = TestClient(app).get("/api/health").json()
    assert body["ok"] is True
    assert body["data"]["ship_name"] == "KM Uji Coba"
    assert body["data"]["agent_version"] == AGENT_VERSION


def test_versi_agent_diteruskan_dari_settings() -> None:
    assert EdgeAgent(_settings()).version == AGENT_VERSION
