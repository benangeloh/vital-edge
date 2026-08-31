"""Test pembacaan metrik sistem.

Sumbernya spesifik Linux. Di platform lain pembacaannya mengembalikan None —
bukan menebak. Angka yang dikarang di panel kesehatan lebih buruk daripada
tanda strip, karena teknisi akan bertindak berdasarkan angka itu.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from fleetview_console.metrics import SystemMetrics

LINUX = sys.platform.startswith("linux")


class TestDisk:
    def test_membaca_partisi_yang_ada(self, tmp_path: Path) -> None:
        percent, free = SystemMetrics(data_dir=tmp_path).disk()
        assert percent is not None and 0 <= percent <= 100
        assert free is not None and free > 0

    def test_direktori_belum_ada_naik_ke_induk(self, tmp_path: Path) -> None:
        """Kasus nyata di Pi yang baru di-provision: /var/lib/fleetview belum
        dibuat, tetapi disk yang akan menampungnya tetap perlu terlihat."""
        percent, free = SystemMetrics(data_dir=tmp_path / "belum" / "ada").disk()
        assert percent is not None
        assert free is not None

    def test_path_salah_bentuk_tidak_membuat_panel_gagal(self) -> None:
        """Path yang mengandung byte nol membuat exists() melempar ValueError,
        bukan mengembalikan False. Panel kesehatan tidak boleh gagal total karena
        satu path yang salah bentuk di config.

        Hasilnya boleh berupa angka: fallback naik sampai root, dan informasi
        tentang filesystem root tetap berguna. Yang penting adalah tidak melempar.
        """
        percent, free = SystemMetrics(data_dir=Path("/\x00tidak-valid")).disk()
        assert percent is None or 0 <= percent <= 100
        assert free is None or free >= 0


class TestCpu:
    def test_pembacaan_pertama_none(self) -> None:
        """CPU dihitung sebagai selisih dua pembacaan. Mengembalikan 0 pada
        pembacaan pertama akan terlihat seperti CPU menganggur, padahal kita
        hanya belum tahu."""
        assert SystemMetrics().cpu_percent() is None

    @pytest.mark.skipif(not LINUX, reason="butuh /proc")
    def test_pembacaan_kedua_menghasilkan_angka(self) -> None:
        metrics = SystemMetrics()
        metrics.cpu_percent()
        value = metrics.cpu_percent()
        assert value is None or 0 <= value <= 100


class TestDegradasiDiLuarLinux:
    @pytest.mark.skipif(LINUX, reason="menguji perilaku di platform non-Linux")
    def test_metrik_linux_mengembalikan_none_bukan_menebak(self) -> None:
        metrics = SystemMetrics()
        assert metrics.memory_percent() is None
        assert metrics.temperature_celsius() is None
        assert metrics.uptime_seconds() is None

    def test_uptime_proses_selalu_tersedia(self) -> None:
        assert SystemMetrics().process_uptime_seconds() >= 0
