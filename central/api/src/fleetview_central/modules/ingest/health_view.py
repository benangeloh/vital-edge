"""Heartbeat terakhir kapal, dalam bentuk yang dikirim ke dashboard."""

from __future__ import annotations

from typing import Any

from fleetview_central.modules.ingest.models import ShipSyncState

__all__ = ["heartbeat_view"]


def heartbeat_view(row: ShipSyncState | None) -> dict[str, Any]:
    """Apa yang terakhir dilaporkan kapal lewat heartbeat.

    Semua nilai None bila kapal belum pernah mengirim heartbeat. Baris sync bisa
    ada hanya karena batch telemetry pernah masuk, jadi keberadaan baris bukan
    bukti ada data kesehatan — dan kesehatan tidak boleh dikarang untuk mengisi
    tampilan.
    """
    if row is None:
        return {
            "last_heartbeat_at": None,
            "agent_version": None,
            "active_transport": None,
            "oldest_pending_age_seconds": None,
            "edge_health": None,
        }
    return {
        "last_heartbeat_at": (row.last_heartbeat_at.isoformat() if row.last_heartbeat_at else None),
        "agent_version": row.agent_version,
        "active_transport": row.active_transport,
        "oldest_pending_age_seconds": row.oldest_pending_age_seconds,
        "edge_health": row.edge_health,
    }
