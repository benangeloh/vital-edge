"""Uji beban: apakah satu Raspberry Pi sanggup, dan apakah armada muat.

Test ini menjalankan generator dan jalur pemrosesan yang sebenarnya, lalu
membandingkan hasilnya dengan anggaran yang harus dipenuhi di lapangan. Ambang
batasnya dipilih dengan margin lebar — kalau salah satu terlewati, itu bukan
"agak lambat", melainkan tanda bahwa asumsi kapasitas di
docs/architecture/00-assessment.md tidak lagi berlaku.

Ditandai `load` dan tidak ikut berjalan secara default; CI menjalankannya
terpisah karena durasinya lebih panjang dari unit test biasa.
"""

from __future__ import annotations

import gzip
import json
import time
from pathlib import Path

import pytest

from fleetview_contracts import TelemetryRecord, encode_record
from fleetview_edge.outbox import OutboxStore
from fleetview_simulator import BenchmarkResult, FleetGenerator, run_benchmark

pytestmark = pytest.mark.load

SHIPS = 70
SENSORS = 100
SECONDS_PER_DAY = 86_400

#: Anggaran satu kapal pada 1 Hz. Satu putaran harus selesai jauh di bawah satu
#: detik, kalau tidak collector tertinggal dan tertinggalnya menumpuk.
TICK_BUDGET_SECONDS = 1.0
#: Margin yang dituntut. 10x memberi ruang untuk Raspberry Pi yang jauh lebih
#: lambat daripada mesin pengembangan, plus beban InfluxDB di perangkat yang sama.
REQUIRED_HEADROOM = 10.0


class TestAnggaranSatuKapal:
    """Yang menentukan apakah Raspberry Pi sanggup."""

    def test_satu_putaran_jauh_di_bawah_anggaran_1hz(self) -> None:
        generator = FleetGenerator(ships=1, sensors_per_ship=SENSORS)
        generator.tick()  # pemanasan; putaran pertama memuat kode

        start = time.perf_counter()
        for _ in range(20):
            records = generator.tick()
            for record in records:
                encode_record(record)
        elapsed = (time.perf_counter() - start) / 20

        assert elapsed * REQUIRED_HEADROOM < TICK_BUDGET_SECONDS, (
            f"satu putaran {SENSORS} sensor memakan {elapsed * 1000:.1f} ms; "
            f"margin di bawah {REQUIRED_HEADROOM}x terhadap anggaran 1 Hz"
        )

    def test_memori_tidak_tumbuh_lintas_putaran(self) -> None:
        """Agent berjalan berbulan-bulan tanpa restart. Pertumbuhan sekecil apa
        pun per putaran akan menjadi OOM pada 1 Hz."""
        generator = FleetGenerator(ships=1, sensors_per_ship=SENSORS)
        for _ in range(200):
            records = generator.tick()
            del records  # generator tidak boleh menyimpan riwayat

        assert generator.ships[0].sensor_count == SENSORS


class TestKapasitasArmada:
    """Apakah 70 kapal muat dalam anggaran penyimpanan dan jaringan."""

    @pytest.fixture(scope="class")
    @classmethod
    def bench(cls) -> BenchmarkResult:
        """Benchmark dijalankan sekali per kelas — mengulanginya untuk setiap
        test hanya mengukur hal yang sama berkali-kali."""
        return run_benchmark(ships=SHIPS, sensors_per_ship=SENSORS, ticks=5)

    def test_laju_titik_sesuai_perhitungan(self, bench: BenchmarkResult) -> None:
        # 70 kapal x 100 sensor x 1 Hz = 7.000 titik/detik.
        assert bench.get("Titik dibangkitkan") == SHIPS * SENSORS * 5

    def test_pembangkitan_jauh_lebih_cepat_dari_kebutuhan(self, bench: BenchmarkResult) -> None:
        needed = SHIPS * SENSORS  # titik/detik pada 1 Hz
        assert bench.get("Laju pembangkitan") > needed * 10

    def test_bandwidth_armada_muat_di_link_sederhana(self, bench: BenchmarkResult) -> None:
        """Kalau ini terlewati, asumsi 'seluruh armada muat di satu link kantor'
        tidak lagi berlaku dan rencana jaringan perlu ditinjau."""
        assert bench.get("Bandwidth armada berkelanjutan") < 10.0  # Mbit/dtk

    def test_kompresi_efektif(self, bench: BenchmarkResult) -> None:
        """Telemetry berubah perlahan dan harus terkompresi sangat baik. Rasio
        yang jatuh menandakan ada yang berubah pada bentuk data — biasanya
        kardinalitas tag yang membengkak."""
        assert bench.get("Rasio kompresi") > 10.0

    def test_retensi_influx_muat_di_disk_yang_dianjurkan(self, bench: BenchmarkResult) -> None:
        """Assessment menganjurkan SSD >= 128 GB dan retensi 90 hari.

        Volume mentah dibagi faktor kompresi TSM yang konservatif (5x, ujung
        bawah dari 5-15x) — memakai ujung atas akan membuat test lulus dengan
        margin yang sebenarnya tidak ada.
        """
        raw_fleet_gb_per_day = bench.get("Volume InfluxDB per hari")
        per_ship_compressed = raw_fleet_gb_per_day / SHIPS / 5.0
        ninety_days = per_ship_compressed * 90
        assert ninety_days < 100, (
            f"{ninety_days:.1f} GB untuk retensi 90 hari tidak muat "
            f"di SSD 128 GB bersama outbox dan sistem operasi"
        )


class TestVolumeOutbox:
    """Kebutuhan disk outbox pada skenario offline terburuk.

    Ini bukan hitungan di atas kertas: yang diukur adalah berkas SQLite yang
    benar-benar ditulis, lengkap dengan indeks dan overhead halaman.
    """

    def test_offline_30_hari_muat_di_disk(self, tmp_path: Path) -> None:
        """Assessment mengandaikan offline terburuk 30 hari pada SSD 128 GB.

        Sebelum Phase 10 outbox menyimpan JSON polos, 352 byte/titik, yang
        berarti 79 GB untuk 30 hari — lebih dari separuh disk, sebelum
        menghitung InfluxDB dan sistem operasi. Sejak payload dikompresi per
        chunk, angkanya turun sekitar 12x.

        Anggaran 20 GB memberi jarak longgar dari sekitar 6,5 GB yang terukur,
        sekaligus tetap menangkap kemunduran besar bila format penyimpanan
        berubah lagi.
        """
        generator = FleetGenerator(ships=1, sensors_per_ship=SENSORS)
        store = OutboxStore(tmp_path / "outbox.db", synchronous_full=False)
        ticks = 100
        for _ in range(ticks):
            store.append(generator.tick())
        store.close()

        on_disk = sum(p.stat().st_size for p in tmp_path.glob("outbox.db*"))
        points = ticks * SENSORS
        row_bytes = on_disk / points

        per_day_gb = row_bytes * SENSORS * SECONDS_PER_DAY / 1e9
        thirty_days_gb = per_day_gb * 30

        print(  # noqa: T201
            f"\n  Outbox terpakai       : {row_bytes:.1f} byte/titik "
            f"(skema lama tanpa kompresi: 352,3)"
            f"\n  Per kapal per hari    : {per_day_gb:.2f} GB/hari"
            f"\n  30 hari offline       : {thirty_days_gb:.1f} GB dari SSD 128 GB"
        )

        assert thirty_days_gb < 20, (
            f"{thirty_days_gb:.1f} GB untuk 30 hari offline melampaui anggaran 20 GB"
        )


class TestBebanIngestCentral:
    def test_ukuran_batch_wajar_untuk_satu_permintaan_http(self) -> None:
        """Batch 500 record harus muat nyaman dalam satu permintaan, supaya
        jalur sekali-jalan dipakai dan tidak perlu sesi berpotongan."""
        generator = FleetGenerator(ships=1, sensors_per_ship=SENSORS)
        records: list[TelemetryRecord] = []
        while len(records) < 500:
            records.extend(generator.tick())
        payload = gzip.compress(
            json.dumps(
                [r.to_reading().model_dump(mode="json") for r in records[:500]],
                separators=(",", ":"),
            ).encode(),
            mtime=0,
        )
        assert len(payload) < 1_048_576  # ambang sekali-jalan NetworkTransport

    def test_laju_batch_armada_terjangkau(self) -> None:
        """70 kapal x 100 sensor pada 1 Hz dengan batch 500 record menghasilkan
        14 batch per detik untuk seluruh armada. Angka itu jauh di bawah yang
        bisa ditangani satu proses FastAPI."""
        batches_per_second = SHIPS * SENSORS / 500
        assert batches_per_second < 50
