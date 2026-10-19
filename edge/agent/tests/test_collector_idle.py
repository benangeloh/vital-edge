"""Collector tanpa sensor terdaftar harus diam, bukan menyambung-putus perangkat.

Instalasi baru mulai dengan registry kosong. Pada LP-A104 itu berbahaya kalau
collector tetap menyambung lalu memutus tiap putaran: panel menahan sesi yang
ditutup di FIN_WAIT_2 sampai slotnya habis, lalu berhenti menjawab Modbus.
"""

from __future__ import annotations

from uuid import UUID

from fleetview_edge.collector import BackoffPolicy, Collector
from fleetview_edge.config import SensorRegistry
from fleetview_edge.protocol import MockLPAAdapter

SHIP_ID = UUID("11111111-1111-1111-1111-111111111111")
DEVICE_ID = UUID("22222222-2222-2222-2222-222222222222")
NO_WAIT = BackoffPolicy(initial_seconds=0.0, max_seconds=0.0, jitter=0.0)


class _SpyAdapter(MockLPAAdapter):
    def __init__(self) -> None:
        super().__init__({"c": 1.0})
        self.connects = 0
        self.reads = 0

    async def connect(self) -> None:
        self.connects += 1
        await super().connect()

    async def read_points(self):  # type: ignore[no-untyped-def]
        self.reads += 1
        return await super().read_points()


async def _sink(_records: object) -> None:
    return None


def _collector(adapter: _SpyAdapter) -> Collector:
    return Collector(
        adapter=adapter,
        registry=SensorRegistry([]),
        sink=_sink,
        ship_id=SHIP_ID,
        device_id=DEVICE_ID,
        poll_interval_seconds=0.001,
        backoff=NO_WAIT,
    )


async def test_tanpa_sensor_perangkat_tidak_disentuh() -> None:
    adapter = _SpyAdapter()
    collector = _collector(adapter)

    for _ in range(5):
        assert await collector.poll_once() == []

    assert adapter.connects == 0
    assert adapter.reads == 0


async def test_tanpa_sensor_penghitung_poll_tetap_maju() -> None:
    """Watchdog systemd menilai collector hidup dari polls_attempted. Diam yang
    tidak menaikkannya akan membuat agent dibunuh dan dinyalakan ulang."""
    collector = _collector(_SpyAdapter())

    for _ in range(3):
        await collector.poll_once()

    assert collector.stats.polls_attempted == 3
    assert collector.stats.polls_failed == 0
