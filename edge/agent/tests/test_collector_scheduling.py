"""Test penjadwalan interval per-sensor.

Berkas terpisah karena penjadwalan punya satu mode kegagalan halus yang layak
diuji sendiri: sensor yang interval-nya sama dengan interval polling sempat
kehilangan setengah sampelnya karena jitter mikrodetik.
"""

from __future__ import annotations

from uuid import UUID

import pytest

from fleetview_contracts import TelemetryRecord
from fleetview_edge.collector import BackoffPolicy, Collector
from fleetview_edge.config import SensorConfig, SensorRegistry
from fleetview_edge.protocol import MockLPAAdapter

SHIP_ID = UUID("11111111-1111-1111-1111-111111111111")
DEVICE_ID = UUID("22222222-2222-2222-2222-222222222222")
NO_WAIT = BackoffPolicy(initial_seconds=0.0, max_seconds=0.0, jitter=0.0)


class Sink:
    def __init__(self) -> None:
        self.records: list[TelemetryRecord] = []

    async def __call__(self, records: list[TelemetryRecord]) -> None:
        self.records.extend(records)


def _build(interval: float, poll_interval: float = 0.001) -> tuple[Collector, Sink]:
    sink = Sink()
    sensor = SensorConfig(sensor_id="s", channel="c", metric="rpm", poll_interval_seconds=interval)
    collector = Collector(
        adapter=MockLPAAdapter({"c": 700.0}),
        registry=SensorRegistry([sensor]),
        sink=sink,
        ship_id=SHIP_ID,
        device_id=DEVICE_ID,
        poll_interval_seconds=poll_interval,
        backoff=NO_WAIT,
    )
    return collector, sink


class TestInterval:
    async def test_sensor_cepat_memancar_setiap_putaran(self) -> None:
        c, sink = _build(interval=1e-6)
        for _ in range(5):
            await c.poll_once()
        assert len(sink.records) == 5

    async def test_sensor_lambat_hanya_memancar_sekali(self) -> None:
        c, sink = _build(interval=3600)
        for _ in range(5):
            await c.poll_once()
        assert len(sink.records) == 1

    async def test_jitter_tidak_membuang_setiap_sampel_kedua(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regresi untuk bug setengah-laju.

        Tanpa toleransi, pembacaan yang datang beberapa mikrodetik lebih awal
        dari tenggatnya akan dibuang, lalu yang berikutnya baru masuk — hasilnya
        setengah laju. Toleransi 10% dari interval menutup celah itu.

        Kasus yang diuji adalah konfigurasi paling lazim dan justru paling
        rentan: interval sensor sama dengan interval polling. Waktu dikendalikan
        agar jitternya pasti, bukan bergantung kebetulan penjadwalan.
        """
        interval_us = 1_000_000  # sensor 1 Hz
        now = [1_000_000_000_000_000]
        monkeypatch.setattr("fleetview_edge.protocol.mock.now_micros", lambda: now[0])

        c, sink = _build(interval=1.0)

        for i in range(6):
            await c.poll_once()
            # Putaran berikutnya datang 1 ms LEBIH AWAL dari sejuta mikrodetik.
            # Inilah yang dulu membuat setiap sampel kedua terbuang.
            now[0] += interval_us - 1_000
            _ = i

        assert len(sink.records) == 6

    async def test_pembacaan_yang_jauh_lebih_awal_tetap_disaring(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Toleransi tidak boleh melonggarkan penjadwalan secara umum — 10%
        lebih awal masih diterima, setengah interval lebih awal tidak."""
        now = [1_000_000_000_000_000]
        monkeypatch.setattr("fleetview_edge.protocol.mock.now_micros", lambda: now[0])

        c, sink = _build(interval=1.0)
        for _ in range(6):
            await c.poll_once()
            now[0] += 500_000  # setengah interval

        assert len(sink.records) == 3

    async def test_sequence_tetap_tanpa_celah_meski_ada_yang_disaring(self) -> None:
        """Nomor sequence hanya dialokasikan untuk record yang benar-benar
        dipancarkan. Kalau dialokasikan lalu dibuang, akan muncul celah — dan
        celah membuat server mengira ada data hilang yang tak pernah ada."""
        sink = Sink()
        collector = Collector(
            adapter=MockLPAAdapter({"c_fast": 1.0, "c_slow": 2.0}),
            registry=SensorRegistry(
                [
                    SensorConfig(
                        sensor_id="fast", channel="c_fast", metric="rpm", poll_interval_seconds=1e-6
                    ),
                    SensorConfig(
                        sensor_id="slow",
                        channel="c_slow",
                        metric="voltage",
                        poll_interval_seconds=3600,
                    ),
                ]
            ),
            sink=sink,
            ship_id=SHIP_ID,
            device_id=DEVICE_ID,
            poll_interval_seconds=0.001,
            backoff=NO_WAIT,
        )
        for _ in range(4):
            await collector.poll_once()

        seqs = sorted(r.sequence_number for r in sink.records)
        assert seqs == list(range(1, len(seqs) + 1))
