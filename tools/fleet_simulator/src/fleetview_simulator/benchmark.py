"""Benchmark beban skala armada — mengukur, bukan menghitung di atas kertas.

Setiap angka di sini berasal dari menjalankan kode produksi yang sebenarnya:
enkoder line protocol yang sama, batcher yang sama, outbox SQLite yang sama.
Yang diekstrapolasi hanya dimensi waktu (dari detik ke hari), dan itu dinyatakan
eksplisit di keluarannya.

CPU dan memori diukur dengan `resource.getrusage`, bukan `/proc`. Alasannya
praktis: benchmark ini juga dijalankan saat pengembangan di macOS, dan angka
yang tersedia di kedua platform lebih berguna daripada angka yang hanya muncul
di satu platform.
"""

from __future__ import annotations

import gzip
import json
import os
import resource
import sqlite3
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path

from fleetview_contracts import TelemetryRecord, encode_record
from fleetview_simulator.generator import FleetGenerator

__all__ = ["BenchmarkResult", "run_benchmark"]

SECONDS_PER_DAY = 86_400
#: getrusage melaporkan maxrss dalam byte di macOS dan kilobyte di Linux.
_RSS_SCALE = 1 if sys.platform == "darwin" else 1024


def format_id(value: float) -> str:
    """Format angka gaya Indonesia: titik untuk ribuan, koma untuk desimal.

    Ditulis eksplisit karena `f"{v:,.2f}".replace(",", ".")` menghasilkan
    "70.000.00" — dua titik dengan arti berbeda dalam satu angka, dan pembaca
    laporan tidak punya cara membedakan mana pemisah ribuan dan mana desimal.
    """
    whole, _, frac = f"{value:,.2f}".partition(".")
    return f"{whole.replace(',', '.')},{frac}"


@dataclass
class Measurement:
    """Satu pengukuran, lengkap dengan bagaimana ia diperoleh."""

    name: str
    value: float
    unit: str
    note: str = ""

    def render(self) -> str:
        return f"  {self.name:<34} {format_id(self.value):>16} {self.unit:<14} {self.note}"


@dataclass
class BenchmarkResult:
    ships: int
    sensors_per_ship: int
    ticks: int
    measurements: list[Measurement] = field(default_factory=list)

    def add(self, name: str, value: float, unit: str, note: str = "") -> None:
        self.measurements.append(Measurement(name, value, unit, note))

    def get(self, name: str) -> float:
        for m in self.measurements:
            if m.name == name:
                return m.value
        raise KeyError(name)

    def render(self) -> str:
        lines = [
            "",
            f"BENCHMARK ARMADA — {self.ships} kapal x {self.sensors_per_ship} sensor",
            f"{self.ticks} putaran diukur; angka per-hari diekstrapolasi dari laju 1 Hz",
            "=" * 88,
        ]
        lines.extend(m.render() for m in self.measurements)
        lines.append("=" * 88)
        return "\n".join(lines)

    def to_json(self) -> str:
        return json.dumps(
            {
                "ships": self.ships,
                "sensors_per_ship": self.sensors_per_ship,
                "ticks": self.ticks,
                "measurements": [asdict(m) for m in self.measurements],
            },
            indent=2,
        )


@contextmanager
def _timed() -> Iterator[list[float]]:
    """Kembalikan [wall_seconds, cpu_seconds] setelah blok selesai."""
    box: list[float] = []
    wall_start = time.perf_counter()
    cpu_start = time.process_time()
    yield box
    box.append(time.perf_counter() - wall_start)
    box.append(time.process_time() - cpu_start)


def _rss_bytes() -> int:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * _RSS_SCALE


def run_benchmark(
    *,
    ships: int = 70,
    sensors_per_ship: int = 100,
    ticks: int = 10,
    batch_size: int = 500,
) -> BenchmarkResult:
    """Jalankan benchmark dan kembalikan hasil pengukuran.

    Args:
        ticks: berapa putaran 1 Hz yang benar-benar dijalankan. Sepuluh putaran
            sudah cukup untuk mengukur laju; menjalankan 86.400 putaran hanya
            akan mengukur hal yang sama selama satu hari penuh.
    """
    result = BenchmarkResult(ships=ships, sensors_per_ship=sensors_per_ship, ticks=ticks)
    rss_before = _rss_bytes()

    generator = FleetGenerator(ships=ships, sensors_per_ship=sensors_per_ship)
    points_per_tick = generator.points_per_tick

    # ---- 1. Pembangkitan ---------------------------------------------------
    batches: list[list[TelemetryRecord]] = []
    with _timed() as gen_timing:
        for _ in range(ticks):
            batches.append(generator.tick())
    gen_wall, gen_cpu = gen_timing
    total_points = sum(len(b) for b in batches)

    result.add(
        "Titik dibangkitkan",
        total_points,
        "titik",
        f"{points_per_tick:,}".replace(",", ".") + " per putaran",
    )
    result.add(
        "Laju pembangkitan",
        total_points / gen_wall,
        "titik/dtk",
        "beban 1 Hz butuh " + f"{points_per_tick:,}".replace(",", ".") + " titik/dtk",
    )

    # ---- 2. Line protocol (jalur InfluxDB) ---------------------------------
    sample = batches[0]
    with _timed() as enc_timing:
        lines = [encode_record(r) for r in sample]
    enc_wall, _ = enc_timing
    line_bytes = sum(len(line.encode()) for line in lines)

    result.add("Laju enkode line protocol", len(sample) / enc_wall, "titik/dtk")
    result.add(
        "Ukuran line protocol",
        line_bytes / len(sample),
        "byte/titik",
        "sebelum kompresi InfluxDB",
    )
    result.add(
        "Volume InfluxDB per hari",
        line_bytes / len(sample) * points_per_tick * SECONDS_PER_DAY / 1e9,
        "GB/hari",
        "mentah; TSM biasanya memampatkan 5-15x",
    )

    # ---- 3. Payload sync (gzip+json) ---------------------------------------
    chunk = sample[:batch_size]
    with _timed() as pack_timing:
        raw = json.dumps(
            [r.to_reading().model_dump(mode="json") for r in chunk], separators=(",", ":")
        ).encode()
        compressed = gzip.compress(raw, mtime=0)
    pack_wall, _ = pack_timing

    result.add("Laju pembentukan batch", len(chunk) / pack_wall, "titik/dtk")
    result.add("Payload batch terkompresi", len(compressed) / len(chunk), "byte/titik")
    result.add(
        "Rasio kompresi",
        len(raw) / len(compressed),
        "x",
        "gzip atas JSON telemetry",
    )

    per_ship_day = len(compressed) / len(chunk) * sensors_per_ship * SECONDS_PER_DAY
    result.add("Bandwidth per kapal per hari", per_ship_day / 1e9, "GB/hari")
    result.add(
        "Bandwidth armada per hari",
        per_ship_day * ships / 1e9,
        "GB/hari",
        "seluruh armada gabungan",
    )
    result.add(
        "Bandwidth armada berkelanjutan",
        per_ship_day * ships / SECONDS_PER_DAY / 1e6 * 8,
        "Mbit/dtk",
        "rata-rata, bukan puncak",
    )

    # ---- 4. Outbox SQLite (jalur durabilitas) ------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "outbox.db"
        conn = sqlite3.connect(path)
        conn.executescript(
            "PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;"
            "CREATE TABLE outbox(sequence INTEGER PRIMARY KEY, captured_at INTEGER,"
            " priority INTEGER, payload TEXT, state TEXT, batch_id TEXT);"
        )
        # Diukur untuk SATU kapal, bukan seluruh armada. Di produksi setiap
        # kapal punya berkas outbox sendiri di Raspberry Pi-nya masing-masing;
        # menulis 70 kapal ke satu database akan mengukur hal yang tidak pernah
        # terjadi, dan angka byte-per-baris-nya pun ikut meleset.
        first_ship = generator.ships[0].ship_id
        ship_records = [r for batch in batches for r in batch if r.ship_id == first_ship]
        rows = [
            (r.sequence_number, r.timestamp, 2, r.model_dump_json(), "pending", None)
            for r in ship_records
        ]
        # Diukur seperti produksi: SATU transaksi per putaran polling, bukan
        # satu transaksi untuk seluruh data. Perbedaannya besar — dengan
        # synchronous=FULL setiap commit memicu fsync, dan fsync itulah biaya
        # sesungguhnya. Mengukur satu transaksi besar melaporkan laju puluhan
        # kali lipat dari yang akan dialami di kapal.
        with _timed() as db_timing:
            for start in range(0, len(rows), sensors_per_ship):
                with conn:
                    conn.executemany(
                        "INSERT INTO outbox VALUES(?,?,?,?,?,?)",
                        rows[start : start + sensors_per_ship],
                    )
        db_wall, _ = db_timing
        conn.close()
        outbox_bytes = path.stat().st_size + sum(
            p.stat().st_size for p in Path(tmp).glob("outbox.db-*")
        )

    result.add(
        "Laju tulis outbox",
        len(rows) / db_wall,
        "titik/dtk",
        "satu kapal, satu commit per putaran (fsync)",
    )
    result.add("Ukuran baris outbox", outbox_bytes / len(rows), "byte/titik")
    # Latensi fsync diukur terpisah karena laju tulis di atas TIDAK bisa
    # dipindahkan begitu saja ke Raspberry Pi. Di macOS, fsync() tidak benar-benar
    # memaksa data ke media (butuh F_FULLFSYNC); di Linux dengan SD card, satu
    # fsync bisa memakan 10-50 ms. Angka ini yang memungkinkan pembaca menskalakan
    # laju tulis ke perangkat kerasnya sendiri.
    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / "fsync.probe"
        with _timed() as fsync_timing, probe.open("wb") as handle:
            for _ in range(50):
                handle.write(b"x" * 4096)
                handle.flush()
                os.fsync(handle.fileno())
        fsync_wall, _ = fsync_timing
    result.add(
        "Latensi fsync",
        fsync_wall / 50 * 1000,
        "ms",
        f"di {sys.platform}; SD card Raspberry Pi lazimnya 10-50 ms",
    )

    result.add(
        "Outbox per kapal per hari",
        outbox_bytes / len(rows) * sensors_per_ship * SECONDS_PER_DAY / 1e9,
        "GB/hari",
        "belum terkompresi; lihat catatan kapasitas di docs/testing-strategy.md",
    )

    # ---- 5. Sumber daya ----------------------------------------------------
    rss_after = _rss_bytes()
    result.add(
        "Puncak memori proses",
        rss_after / 1e6,
        "MB",
        f"naik {(rss_after - rss_before) / 1e6:.1f} MB selama benchmark",
    )
    result.add(
        "CPU pembangkitan",
        gen_cpu / max(gen_wall, 1e-9) * 100,
        "% satu inti",
        f"untuk {points_per_tick:,}".replace(",", ".") + " titik/putaran",
    )

    # Beban CPU satu kapal pada 1 Hz — inilah angka yang menentukan apakah
    # Raspberry Pi sanggup, dan satu-satunya angka yang benar-benar penting
    # untuk keputusan perangkat keras.
    per_ship_cpu = gen_cpu / max(gen_wall, 1e-9) / ships * 100
    result.add(
        "CPU per kapal (perkiraan)",
        per_ship_cpu,
        "% satu inti",
        "hanya pembangkitan; belum termasuk I/O",
    )

    return result
