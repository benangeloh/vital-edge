"""Menyiapkan hasil pencarian channel untuk ditampilkan di Console."""

from __future__ import annotations

from typing import Any

from fleetview_edge.config.sensor_editor import UW_FREE_FIRST, UW_FREE_LAST
from fleetview_edge.protocol.base import DiscoveredChannel

__all__ = ["annotate"]


def _identity(channel: str) -> str:
    """Bentuk pembanding: `uw00050` dan `UW50` adalah alamat yang sama."""
    upper = channel.strip().upper()
    if upper.startswith("UW") and upper[2:].isdigit():
        return f"UW{int(upper[2:])}"
    return channel.strip()


def _uw_number(channel: str) -> int | None:
    upper = channel.strip().upper()
    if upper.startswith("UW") and upper[2:].isdigit():
        return int(upper[2:])
    return None


def annotate(
    found: list[DiscoveredChannel], entries: list[dict[str, Any]], *, adapter: str
) -> list[dict[str, Any]]:
    """Buang yang sudah terdaftar, tandai kemungkinan sensor, dan urutkan.

    Pada LP-A104, blok UW1000-UW1099 adalah tempat baris salin sensor ditaruh
    (docs/hardware/konvensi-atlogic.md); register di luarnya kemungkinan besar
    milik panel sendiri. Itu petunjuk, bukan keputusan: teknisi yang memilih.
    """
    terdaftar = {_identity(str(e.get("channel", ""))) for e in entries}
    hasil: list[dict[str, Any]] = []
    for f in found:
        if _identity(f.channel) in terdaftar:
            continue
        nomor = _uw_number(f.channel)
        likely = (
            True
            if adapter != "lp_a104" or nomor is None
            else UW_FREE_FIRST <= nomor <= UW_FREE_LAST
        )
        hasil.append(
            {
                "channel": f.channel,
                "value": f.value,
                "changing": f.changing,
                "likely_sensor": likely,
                "_urut": nomor if nomor is not None else -1,
            }
        )
    hasil.sort(key=lambda h: (not h["likely_sensor"], h["_urut"], h["channel"]))
    for h in hasil:
        del h["_urut"]
    return hasil
