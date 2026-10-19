"""Test tampilan heartbeat terakhir kapal, untuk tab Kesehatan Perangkat."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from fleetview_central.modules.ingest.health_view import heartbeat_view
from fleetview_central.modules.ingest.models import ShipSyncState

_HEALTH = {
    "disk_used_percent": 62.5,
    "memory_used_percent": 40.0,
    "cpu_used_percent": 12.0,
    "uptime_seconds": 3600,
    "collector_healthy": True,
    "storage_healthy": True,
    "protocol_link_healthy": False,
    "clock_skew_seconds": 0.4,
}


def test_kapal_tanpa_baris_sync_belum_pernah_heartbeat() -> None:
    assert heartbeat_view(None) == {
        "last_heartbeat_at": None,
        "agent_version": None,
        "active_transport": None,
        "oldest_pending_age_seconds": None,
        "edge_health": None,
    }


def test_baris_tanpa_heartbeat_tidak_mengarang_kesehatan() -> None:
    """Baris ada karena batch pernah masuk, bukan karena heartbeat."""
    row = ShipSyncState(ship_id=uuid4())

    view = heartbeat_view(row)

    assert view["last_heartbeat_at"] is None
    assert view["edge_health"] is None


def test_heartbeat_diteruskan_apa_adanya() -> None:
    row = ShipSyncState(
        ship_id=uuid4(),
        last_heartbeat_at=datetime(2026, 10, 19, 3, 0, tzinfo=UTC),
        agent_version="0.1.0",
        active_transport="lan",
        oldest_pending_age_seconds=42,
        edge_health=_HEALTH,
    )

    view = heartbeat_view(row)

    assert view["last_heartbeat_at"] == "2026-10-19T03:00:00+00:00"
    assert view["agent_version"] == "0.1.0"
    assert view["active_transport"] == "lan"
    assert view["oldest_pending_age_seconds"] == 42
    assert view["edge_health"] == _HEALTH
