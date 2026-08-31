"""Integration test: Collector -> StorageWriter -> penyimpanan.

Merangkai jalur lengkap persis seperti di produksi, dengan simulator sebagai
sumber data dan penyimpanan in-memory sebagai tujuan. Yang dibuktikan di sini
bukan perilaku satu komponen, melainkan perilaku gabungannya — terutama bahwa
penyimpanan yang bermasalah tidak menghentikan akuisisi.

Empat besaran yang disyaratkan disimulasikan: RPM, fuel, pressure, temperature.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import UUID

import pytest

from fleetview_contracts import AcquisitionSource, TelemetryRecord
from fleetview_edge.collector import BackoffPolicy, Collector
from fleetview_edge.config import load_sensor_registry
from fleetview_edge.protocol import EnginePhase, SimulatorAdapter
from fleetview_edge.storage import (
    InfluxTelemetryStore,
    InMemoryTelemetryStore,
    StorageState,
    StorageWriter,
    encode_records,
)

SHIP = UUID("11111111-1111-1111-1111-111111111111")
DEVICE = UUID("22222222-2222-2222-2222-222222222222")
NO_WAIT = BackoffPolicy(initial_seconds=0.0, max_seconds=0.0, jitter=0.0)

REPO_ROOT = Path(__file__).resolve().parents[3]
SENSORS = REPO_ROOT / "edge" / "agent" / "config" / "sensors.example.yaml"

#: Empat besaran yang disyaratkan Phase 3.
REQUIRED_METRICS = {"rpm", "fuel_level", "pressure", "temperature"}


def _pipeline(
    store: InMemoryTelemetryStore, *, phase: EnginePhase = EnginePhase.CRUISE
) -> tuple[Collector, StorageWriter]:
    writer = StorageWriter(store, buffer_max_records=5_000)
    collector = Collector(
        adapter=SimulatorAdapter(phase=phase, seed=42),
        registry=load_sensor_registry(SENSORS),
        sink=writer,
        ship_id=SHIP,
        device_id=DEVICE,
        poll_interval_seconds=0.01,
        backoff=NO_WAIT,
        source=AcquisitionSource.SIMULATED,
    )
    return collector, writer


class TestJalurLengkap:
    async def test_keempat_besaran_yang_disyaratkan_tersimpan(self) -> None:
        store = InMemoryTelemetryStore()
        collector, _ = _pipeline(store)
        await collector.poll_once()

        metrics = {r.metric for r in store.records}
        assert metrics >= REQUIRED_METRICS, f"kurang: {REQUIRED_METRICS - metrics}"

    async def test_nilai_simulasi_masuk_akal(self) -> None:
        store = InMemoryTelemetryStore()
        collector, _ = _pipeline(store)
        await collector.poll_once()

        by_sensor = {r.sensor_id: r for r in store.records}
        rpm = by_sensor["me_port_rpm"].value
        fuel = by_sensor["fuel_level_main"].value
        press = by_sensor["me_port_lube_pressure"].value
        temp = by_sensor["me_port_exhaust_temp"].value

        assert isinstance(rpm, float) and 1200 < rpm < 1700
        assert isinstance(fuel, float) and 0 <= fuel <= 100
        assert isinstance(press, float) and 0 < press < 10
        assert isinstance(temp, float) and 300 < temp < 500

    async def test_identitas_lengkap_di_setiap_record(self) -> None:
        store = InMemoryTelemetryStore()
        collector, _ = _pipeline(store)
        await collector.poll_once()

        for r in store.records:
            assert r.ship_id == SHIP
            assert r.device_id == DEVICE
            assert r.sensor_id
            assert r.sequence_number > 0
            assert r.timestamp > 0

    async def test_data_simulasi_ditandai_sebagai_simulasi(self) -> None:
        """Supaya tidak akan pernah tertukar dengan data sungguhan."""
        store = InMemoryTelemetryStore()
        collector, _ = _pipeline(store)
        await collector.poll_once()
        assert all(r.source is AcquisitionSource.SIMULATED for r in store.records)

    async def test_bisa_dienkode_ke_line_protocol(self) -> None:
        """Bukti bahwa apa pun yang dihasilkan jalur ini bisa benar-benar
        dikirim ke InfluxDB."""
        store = InMemoryTelemetryStore()
        collector, _ = _pipeline(store)
        await collector.poll_once()

        payload = encode_records(store.records)
        assert len(payload.splitlines()) == len(store.records)
        for line in payload.splitlines():
            assert f"ship_id={SHIP}" in line
            assert line.rstrip().split(" ")[-1].isdigit()  # timestamp di akhir

    async def test_gps_tersimpan_sebagai_satu_titik_multi_field(self) -> None:
        store = InMemoryTelemetryStore()
        collector, _ = _pipeline(store)
        await collector.poll_once()

        gps = next(r for r in store.records if r.metric == "position")
        assert set(gps.values) == {"lat", "lon", "sog", "cog"}

    async def test_mesin_mati_menghasilkan_nol_bukan_ketiadaan_data(self) -> None:
        """Mesin mati itu informasi, bukan ketiadaan informasi. Kalau sensornya
        berhenti melapor saat mesin mati, kita tidak bisa membedakannya dari
        sensor yang rusak."""
        store = InMemoryTelemetryStore()
        collector, _ = _pipeline(store, phase=EnginePhase.STOPPED)
        await collector.poll_once()

        rpm = next(r for r in store.records if r.sensor_id == "me_port_rpm")
        assert rpm.value == 0.0


class TestKetahananPenyimpanan:
    async def test_penyimpanan_mati_tidak_menghentikan_akuisisi(self) -> None:
        """Ini jaminan inti Phase 3. InfluxDB yang sedang compaction tidak boleh
        membuat kapal buta."""
        store = InMemoryTelemetryStore(unavailable=True)
        collector, writer = _pipeline(store)

        for _ in range(3):
            await collector.poll_once()

        # Yang penting bukan setiap putaran menghasilkan record — interval
        # per-sensor memang menyaring sebagian. Yang penting adalah tidak ada
        # satu pun putaran yang GAGAL gara-gara penyimpanan bermasalah.
        assert collector.stats.polls_attempted == 3
        assert collector.stats.polls_succeeded == 3
        assert collector.stats.polls_failed == 0
        assert writer.buffered > 0  # datanya tertahan
        assert writer.dropped == 0  # tidak ada yang hilang

    async def test_data_tersangga_lalu_tersimpan_saat_pulih(self) -> None:
        store = InMemoryTelemetryStore(unavailable=True)
        collector, writer = _pipeline(store)

        await collector.poll_once()
        buffered = writer.buffered
        assert buffered > 0
        assert store.records == []

        store.recover()
        assert await writer.flush() == 0
        assert len(store.records) == buffered

    async def test_urutan_sequence_terjaga_melewati_gangguan(self) -> None:
        store = InMemoryTelemetryStore(unavailable=True)
        collector, writer = _pipeline(store)

        await collector.poll_once()
        await collector.poll_once()
        store.recover()
        await collector.poll_once()
        await writer.flush()

        seqs = [r.sequence_number for r in store.records]
        assert seqs == sorted(seqs)
        assert len(set(seqs)) == len(seqs)

    async def test_loop_terus_berjalan_meski_penyimpanan_mati(self) -> None:
        store = InMemoryTelemetryStore(unavailable=True)
        collector, writer = _pipeline(store)

        task = asyncio.create_task(collector.run())
        await asyncio.sleep(0.06)
        collector.stop()
        await asyncio.wait_for(task, timeout=1.0)

        assert collector.stats.polls_succeeded >= 2
        assert writer.buffered > 0

    async def test_kesehatan_melaporkan_kondisi_sesungguhnya(self) -> None:
        store = InMemoryTelemetryStore(unavailable=True)
        collector, writer = _pipeline(store)
        await collector.poll_once()

        health = await writer.health()
        assert health.state is StorageState.UNAVAILABLE
        assert health.buffered_records > 0
        assert health.dropped_records == 0

        store.recover()
        await writer.flush()

        health = await writer.health()
        assert health.state is StorageState.HEALTHY
        assert health.buffered_records == 0

    async def test_buffer_meluap_dihitung_sebagai_kehilangan_data(self) -> None:
        """Kalau `dropped` tidak nol, ada data yang benar-benar hilang. Angka ini
        harus terlihat, bukan tersembunyi."""
        store = InMemoryTelemetryStore(unavailable=True)
        writer = StorageWriter(store, buffer_max_records=5)
        collector = Collector(
            adapter=SimulatorAdapter(seed=1),
            registry=load_sensor_registry(SENSORS),
            sink=writer,
            ship_id=SHIP,
            device_id=DEVICE,
            poll_interval_seconds=0.01,
            backoff=NO_WAIT,
        )
        for _ in range(3):
            await collector.poll_once()

        health = await writer.health()
        assert health.buffered_records == 5
        assert health.dropped_records > 0


class TestPembuatanLewatEdgeAgent:
    def test_agent_merakit_writer(self) -> None:
        from fleetview_edge.app import EdgeAgent
        from fleetview_edge.settings import EdgeSettings

        settings = EdgeSettings(
            ship={"ship_id": str(SHIP), "ship_name": "KM Uji", "device_id": str(DEVICE)},
            storage={"buffer_max_records": 123},
        )
        writer = EdgeAgent(settings).build_writer(InMemoryTelemetryStore())
        assert writer.buffered == 0

    def test_retensi_diambil_dari_config(self) -> None:
        from fleetview_edge.app import EdgeAgent
        from fleetview_edge.settings import EdgeSettings

        settings = EdgeSettings(
            ship={"ship_id": str(SHIP), "ship_name": "KM Uji", "device_id": str(DEVICE)},
            storage={"retention_days": 45},
        )
        store = EdgeAgent(settings).build_store()
        assert isinstance(store, InfluxTelemetryStore)
        assert store.retention.days == 45


@pytest.mark.integration
class TestInfluxSungguhan:
    """Butuh InfluxDB yang berjalan (`make up`). Dilewati secara default."""

    async def test_penulisan_ke_influx_lokal(self) -> None:
        import httpx

        from fleetview_edge.storage import InfluxTelemetryStore, RetentionPolicy

        store = InfluxTelemetryStore(
            url="http://localhost:8086",
            org="fleetview",
            bucket="telemetry",
            token="fleetview-dev-token",
            retention=RetentionPolicy(days=7),
        )
        try:
            health = await store.health()
            if not health.reachable:
                pytest.skip("InfluxDB tidak berjalan; jalankan 'make up'")

            await store.ensure_ready()
            records: list[TelemetryRecord] = [
                TelemetryRecord(
                    ship_id=SHIP,
                    device_id=DEVICE,
                    sensor_id="me_port_rpm",
                    sequence_number=1,
                    timestamp=1_756_612_800_000_000,
                    metric="rpm",
                    values={"value": 742.5},
                    unit="rpm",
                )
            ]
            await store.write(records)
        except httpx.HTTPError:
            pytest.skip("InfluxDB tidak bisa dijangkau")
        finally:
            await store.close()
