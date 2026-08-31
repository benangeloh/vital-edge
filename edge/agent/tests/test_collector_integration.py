"""Integration test: jalur akuisisi lengkap memakai MockLPAAdapter.

Merangkai adapter, parser, validator, clock, dan supervisor persis seperti di
produksi — yang diganti hanya sumber datanya. Yang diuji di sini adalah
perilaku yang muncul dari gabungan komponen, bukan dari salah satunya:
pemulihan sambungan, penanganan kegagalan sebagian, dan kelangsungan sequence
melewati gangguan.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import pytest

from fleetview_contracts import AcquisitionSource, Quality, TelemetryRecord
from fleetview_edge.collector import BackoffPolicy, Collector
from fleetview_edge.config import SensorConfig, SensorRegistry
from fleetview_edge.protocol import MockLPAAdapter, MockScript

SHIP_ID = UUID("11111111-1111-1111-1111-111111111111")
DEVICE_ID = UUID("22222222-2222-2222-2222-222222222222")

# Backoff nol supaya test tidak benar-benar menunggu; logika pemulihannya sama.
NO_WAIT = BackoffPolicy(initial_seconds=0.0, max_seconds=0.0, jitter=0.0)

EVERY_POLL = 1e-6
"""Interval sensor yang jauh lebih kecil dari waktu satu putaran, sehingga
sensor uji selalu jatuh tempo dan interval per-sensor tidak ikut mengaburkan
hasil test yang sedang menguji hal lain."""


class Sink:
    """Penampung in-memory. Phase 3 menggantinya dengan outbox SQLite."""

    def __init__(self) -> None:
        self.records: list[TelemetryRecord] = []
        self.batches: list[list[TelemetryRecord]] = []

    async def __call__(self, records: list[TelemetryRecord]) -> None:
        self.records.extend(records)
        self.batches.append(records)


def _registry(*sensors: SensorConfig) -> SensorRegistry:
    return SensorRegistry(list(sensors))


def _sensor(**over: object) -> SensorConfig:
    base: dict[str, object] = {
        "sensor_id": "me_port_rpm",
        "channel": "ch_rpm",
        "metric": "rpm",
        "unit": "rpm",
        "poll_interval_seconds": EVERY_POLL,
    }
    return SensorConfig(**{**base, **over})


def _collector(adapter: MockLPAAdapter, registry: SensorRegistry, sink: Sink) -> Collector:
    return Collector(
        adapter=adapter,
        registry=registry,
        sink=sink,
        ship_id=SHIP_ID,
        device_id=DEVICE_ID,
        poll_interval_seconds=0.01,
        poll_timeout_seconds=0.5,
        backoff=NO_WAIT,
    )


class TestJalurBahagia:
    async def test_record_membawa_seluruh_field_yang_disyaratkan(self) -> None:
        """Persyaratan Phase 2: ship_id, device_id, sensor_id, timestamp, metric,
        value, unit, quality, sequence_number."""
        sink = Sink()
        c = _collector(MockLPAAdapter({"ch_rpm": 742.5}), _registry(_sensor()), sink)
        await c.poll_once()

        r = sink.records[0]
        assert r.ship_id == SHIP_ID
        assert r.device_id == DEVICE_ID
        assert r.sensor_id == "me_port_rpm"
        assert r.timestamp > 0
        assert r.metric == "rpm"
        assert r.value == pytest.approx(742.5)
        assert r.unit == "rpm"
        assert r.quality is Quality.GOOD
        assert r.sequence_number == 1

    async def test_skala_diterapkan_lewat_config(self) -> None:
        sink = Sink()
        c = _collector(MockLPAAdapter({"ch_rpm": 7425}), _registry(_sensor(scale=0.1)), sink)
        await c.poll_once()
        assert sink.records[0].value == pytest.approx(742.5)

    async def test_sequence_naik_dan_tanpa_celah_lintas_putaran(self) -> None:
        sink = Sink()
        c = _collector(
            MockLPAAdapter({"ch_rpm": 700.0, "ch_v": 24.0}),
            _registry(_sensor(), _sensor(sensor_id="bus_v", channel="ch_v", metric="voltage")),
            sink,
        )
        for _ in range(5):
            await c.poll_once()

        seqs = [r.sequence_number for r in sink.records]
        assert seqs == list(range(1, len(seqs) + 1))

    async def test_gps_multi_field_jadi_satu_record(self) -> None:
        """lat dan lon harus berada di titik yang sama — kalau terpisah, salah
        satunya bisa hilang dan menghasilkan koordinat yang tidak konsisten."""
        sink = Sink()
        c = _collector(
            MockLPAAdapter({"c_lat": -7.25, "c_lon": 112.75, "c_sog": 12.4}),
            _registry(
                _sensor(
                    sensor_id="gps", channel="c_lat", metric="position", field_name="lat", unit=None
                ),
                _sensor(
                    sensor_id="gps", channel="c_lon", metric="position", field_name="lon", unit=None
                ),
                _sensor(
                    sensor_id="gps", channel="c_sog", metric="position", field_name="sog", unit=None
                ),
            ),
            sink,
        )
        await c.poll_once()

        assert len(sink.records) == 1
        assert set(sink.records[0].values) == {"lat", "lon", "sog"}
        assert sink.records[0].value is None  # multi-field: tidak ada nilai tunggal

    async def test_bisa_diubah_ke_bentuk_wire(self) -> None:
        sink = Sink()
        c = _collector(MockLPAAdapter({"ch_rpm": 742.5}), _registry(_sensor()), sink)
        await c.poll_once()

        reading = sink.records[0].to_reading()
        assert reading.seq == 1
        assert reading.measurement == "rpm"
        assert reading.fields == {"value": pytest.approx(742.5)}


class TestKualitas:
    async def test_nilai_di_luar_batas_disimpan_dengan_flag(self) -> None:
        """Ini inti dari seluruh pendekatan: sensor rusak harus bisa dibedakan
        dari kapal yang tidak mengirim data."""
        sink = Sink()
        c = _collector(
            MockLPAAdapter({"ch_rpm": 99999.0}),
            _registry(_sensor(validation={"min_value": 0, "max_value": 2000})),
            sink,
        )
        await c.poll_once()

        assert len(sink.records) == 1  # tidak dibuang
        assert sink.records[0].quality is Quality.SUSPECT
        assert sink.records[0].value == pytest.approx(99999.0)  # nilai aslinya utuh

    async def test_source_ditandai_di_setiap_record(self) -> None:
        sink = Sink()
        c = _collector(MockLPAAdapter({"ch_rpm": 700.0}), _registry(_sensor()), sink)
        await c.poll_once()
        assert sink.records[0].source is AcquisitionSource.LIVE


class TestKegagalan:
    async def test_timeout_tidak_menghentikan_collector(self) -> None:
        adapter = MockLPAAdapter({"ch_rpm": 700.0}, script=MockScript(timeout_on=[0]))
        sink = Sink()
        c = _collector(adapter, _registry(_sensor()), sink)

        assert await c.poll_once() == []  # timeout ditelan
        assert len(await c.poll_once()) == 1  # putaran berikutnya pulih
        assert c.stats.polls_failed == 1
        assert c.stats.polls_succeeded == 1

    async def test_putus_sambungan_lalu_tersambung_kembali(self) -> None:
        adapter = MockLPAAdapter({"ch_rpm": 700.0}, script=MockScript(disconnect_on=[0]))
        sink = Sink()
        c = _collector(adapter, _registry(_sensor()), sink)

        assert await c.poll_once() == []
        assert adapter.is_connected is False
        assert len(await c.poll_once()) == 1  # supervisor menyambung ulang
        assert c.stats.reconnects >= 1

    async def test_sequence_tidak_terbuang_saat_pembacaan_gagal(self) -> None:
        """Nomor sequence hanya dialokasikan untuk data yang benar-benar ada.
        Kalau terbuang saat gagal, akan muncul celah — dan celah membuat server
        mengira ada data hilang yang sebenarnya tidak pernah ada."""
        adapter = MockLPAAdapter({"ch_rpm": 700.0}, script=MockScript(timeout_on=[1, 2]))
        sink = Sink()
        c = _collector(adapter, _registry(_sensor()), sink)

        for _ in range(5):
            await c.poll_once()

        seqs = [r.sequence_number for r in sink.records]
        assert seqs == list(range(1, len(seqs) + 1))

    async def test_kegagalan_sebagian_tetap_mengumpulkan_sisanya(self) -> None:
        """Satu sensor rusak tidak boleh membutakan seluruh kapal."""
        adapter = MockLPAAdapter(
            {"ch_rpm": 700.0, "ch_v": 24.0, "ch_t": 380.0},
            script=MockScript(fail_channels_on={0: ["ch_v"]}),
        )
        sink = Sink()
        c = _collector(
            adapter,
            _registry(
                _sensor(),
                _sensor(sensor_id="bus_v", channel="ch_v", metric="voltage"),
                _sensor(sensor_id="exh_t", channel="ch_t", metric="temperature"),
            ),
            sink,
        )
        records = await c.poll_once()
        assert {r.sensor_id for r in records} == {"me_port_rpm", "exh_t"}

    async def test_connect_gagal_berulang_lalu_pulih(self) -> None:
        adapter = MockLPAAdapter({"ch_rpm": 700.0}, script=MockScript(connect_failures=3))
        sink = Sink()
        c = _collector(adapter, _registry(_sensor()), sink)

        for _ in range(3):
            assert await c.poll_once() == []
        assert len(await c.poll_once()) == 1

    async def test_reconnect_mereset_riwayat_validator(self) -> None:
        """Setelah putus lama, nilai lama tidak relevan. Menyimpannya membuat
        pembacaan pertama setelah pulih tampak seperti lonjakan luar biasa."""
        adapter = MockLPAAdapter({"ch_rpm": 700.0}, script=MockScript(disconnect_on=[1]))
        sink = Sink()
        c = _collector(adapter, _registry(_sensor(validation={"max_rate_per_second": 10})), sink)
        await c.poll_once()  # 700, baseline
        await c.poll_once()  # putus
        adapter.set_value("ch_rpm", 1500.0)
        records = await c.poll_once()  # tersambung ulang, lompatan besar

        assert records[0].quality is Quality.GOOD  # bukan salah sensornya

    async def test_channel_tak_dikenal_dicatat_bukan_digagalkan(self) -> None:
        sink = Sink()
        c = _collector(
            MockLPAAdapter({"ch_rpm": 700.0, "ch_misterius": 1.0}),
            _registry(_sensor()),
            sink,
        )
        records = await c.poll_once()
        assert len(records) == 1
        assert "ch_misterius" in c.stats.unknown_channels


class TestIntervalPerSensor:
    async def test_sensor_lambat_tidak_dipancarkan_setiap_putaran(self) -> None:
        """Level bahan bakar pada 1 Hz hanyalah 5x data tanpa 5x informasi —
        dan pada kapal yang offline 30 hari, itu langsung terasa di disk."""
        sink = Sink()
        c = _collector(
            MockLPAAdapter({"ch_rpm": 700.0, "ch_fuel": 84.0}),
            _registry(
                _sensor(poll_interval_seconds=EVERY_POLL),
                _sensor(
                    sensor_id="fuel",
                    channel="ch_fuel",
                    metric="fuel_level",
                    poll_interval_seconds=3600,
                ),
            ),
            sink,
        )
        for _ in range(4):
            await c.poll_once()

        counts: dict[str, int] = {}
        for r in sink.records:
            counts[r.sensor_id] = counts.get(r.sensor_id, 0) + 1
        assert counts["me_port_rpm"] == 4
        assert counts["fuel"] == 1  # hanya putaran pertama


class TestLoop:
    async def test_run_mengumpulkan_lalu_berhenti_dengan_rapi(self) -> None:
        adapter = MockLPAAdapter({"ch_rpm": 700.0})
        sink = Sink()
        c = _collector(adapter, _registry(_sensor()), sink)

        task = asyncio.create_task(c.run())
        await asyncio.sleep(0.06)
        c.stop()
        await asyncio.wait_for(task, timeout=1.0)

        assert len(sink.records) >= 2
        assert adapter.is_connected is False  # close() dipanggil saat keluar

    async def test_snapshot_health_melaporkan_kondisi(self) -> None:
        sink = Sink()
        c = _collector(MockLPAAdapter({"ch_rpm": 700.0}), _registry(_sensor()), sink)
        await c.poll_once()

        health = await c.snapshot_health()
        assert health["adapter"] == "mock"
        assert health["connected"] is True
        assert health["polls_succeeded"] == 1
        assert health["records_emitted"] == 1
        assert health["current_sequence"] == 1


class TestSiklusHidup:
    async def test_state_supervisor_ikut_bersih_setelah_berhenti(self) -> None:
        """Setelah run() menutup adapter, supervisor tidak boleh mengira masih
        tersambung — kalau tidak, menjalankan collector lagi akan melewatkan
        connect() dan pembacaan pertamanya gagal."""
        adapter = MockLPAAdapter({"ch_rpm": 700.0})
        sink = Sink()
        c = _collector(adapter, _registry(_sensor()), sink)

        task = asyncio.create_task(c.run())
        await asyncio.sleep(0.03)
        c.stop()
        await asyncio.wait_for(task, timeout=1.0)

        assert adapter.is_connected is False
        health = await c.snapshot_health()
        assert health["connected"] is False

    async def test_bisa_dijalankan_ulang_setelah_berhenti(self) -> None:
        adapter = MockLPAAdapter({"ch_rpm": 700.0})
        sink = Sink()
        c = _collector(adapter, _registry(_sensor()), sink)

        task = asyncio.create_task(c.run())
        await asyncio.sleep(0.03)
        c.stop()
        await asyncio.wait_for(task, timeout=1.0)
        before = len(sink.records)

        c._stopping.clear()
        records = await c.poll_once()

        assert len(records) == 1
        assert len(sink.records) > before


class TestPenghitungTimeout:
    async def test_timeout_dari_adapter_ikut_terhitung(self, store: object = None) -> None:
        """Adapter perangkat sungguhan mendeteksi timeout-nya sendiri dan
        melempar ProtocolTimeoutError. Kalau hanya timeout asyncio yang dihitung,
        penghitung di Console selalu nol padahal polling benar-benar timeout."""
        _ = store
        adapter = MockLPAAdapter({"ch_rpm": 700.0}, script=MockScript(timeout_on=[0, 1]))
        sink = Sink()
        c = _collector(adapter, _registry(_sensor()), sink)

        await c.poll_once()
        await c.poll_once()

        assert c.stats.timeouts == 2
        assert c.stats.polls_failed == 2
