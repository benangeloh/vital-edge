"""Ringkasan pengiriman data ke pusat di halaman Ringkasan.

Pembaca baru hanya perlu dua angka: berapa yang sudah tersimpan di pusat dan
berapa yang belum. Istilah teknis (batch, karantina, transport) tinggal di
halaman Sinkronisasi.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from fleetview_common import now_micros
from fleetview_console import create_console_app
from fleetview_console.context import SyncSnapshot


def _client(context: Any) -> TestClient:
    return TestClient(
        create_console_app(
            context=context,
            ship_name="KM Sinar Jaya",
            ship_id="11111111-1111-1111-1111-111111111111",
            agent_version="0.1.0",
            environment="production",
        )
    )


def _ringkasan(make_context: Callable[..., Any], **sync: Any) -> str:
    base: dict[str, Any] = {
        "pending_records": 0,
        "pending_batches": 0,
        "oldest_pending_age_seconds": None,
        "last_success_us": now_micros() - 7_000_000,
        "last_error": None,
        "active_transport": "lan",
        "quarantined_batches": 0,
        "records_synced": 0,
        "bytes_sent": 0,
        "records_acked": 839_968,
        "records_stuck": 0,
        "acked_size_bytes": 0,
        "unsent_size_bytes": 0,
        "stuck_size_bytes": 0,
    }
    snapshot = SyncSnapshot(**{**base, **sync})
    context = make_context()

    async def sync_snapshot() -> SyncSnapshot:
        return snapshot

    context.sync = sync_snapshot
    html = _client(context).get("/").text
    # Hanya kartu pengiriman: halaman ini juga memuat kartu lain.
    return html.split("Pengiriman data ke pusat", 1)[1].split("</section>", 1)[0]


class TestDuaAngka:
    def test_sudah_dan_belum_terkirim_tampil_dengan_judul_jelas(
        self, make_context: Callable[..., Any]
    ) -> None:
        kartu = _ringkasan(make_context, pending_records=8)

        assert "Sudah terkirim" in kartu
        assert "839.968" in kartu
        assert "Belum terkirim" in kartu
        assert re.search(r">\s*8\s*<", kartu)

    def test_belum_terkirim_mencakup_data_yang_tertahan(
        self, make_context: Callable[..., Any]
    ) -> None:
        kartu = _ringkasan(
            make_context, pending_records=8, records_stuck=343_294, quarantined_batches=4712
        )

        assert "343.302" in kartu  # 8 menunggu + 343.294 tertahan
        assert "8 menunggu" in kartu
        assert "343.294 tertahan" in kartu

    def test_persentase_terkirim_ditampilkan(self, make_context: Callable[..., Any]) -> None:
        kartu = _ringkasan(make_context, pending_records=1_000, records_acked=9_000)

        assert "90,0%" in kartu

    def test_batang_kemajuan_punya_label_untuk_pembaca_layar(
        self, make_context: Callable[..., Any]
    ) -> None:
        kartu = _ringkasan(make_context, pending_records=1_000, records_acked=9_000)

        assert 'class="meter"' in kartu
        assert 'aria-label="90,0% data sudah terkirim ke pusat"' in kartu

    def test_tanpa_data_sama_sekali_tidak_menampilkan_persen(
        self, make_context: Callable[..., Any]
    ) -> None:
        kartu = _ringkasan(make_context, records_acked=0, pending_records=0)

        assert "%" not in kartu

    def test_jangka_waktu_angka_terkirim_dinyatakan_jujur(
        self, make_context: Callable[..., Any]
    ) -> None:
        """Data lama yang sudah dikonfirmasi dipangkas dari kapal setelah beberapa hari."""
        assert "7 hari terakhir" in _ringkasan(make_context)


class TestStatusSatuKalimat:
    def test_semua_terkirim(self, make_context: Callable[..., Any]) -> None:
        assert "Semua data sudah terkirim" in _ringkasan(make_context)

    def test_sedang_mengirim(self, make_context: Callable[..., Any]) -> None:
        assert "Sedang mengirim" in _ringkasan(make_context, pending_records=40)

    def test_ada_data_tertahan(self, make_context: Callable[..., Any]) -> None:
        kartu = _ringkasan(make_context, records_stuck=500, quarantined_batches=5)

        assert "Ada data tertahan" in kartu
        assert "Semua data sudah terkirim" not in kartu

    def test_belum_pernah_berhasil_mengirim(self, make_context: Callable[..., Any]) -> None:
        kartu = _ringkasan(make_context, pending_records=40, last_success_us=None)

        assert "Belum pernah berhasil mengirim" in kartu

    def test_terakhir_berhasil_ditampilkan(self, make_context: Callable[..., Any]) -> None:
        assert "Terakhir berhasil mengirim" in _ringkasan(make_context)


class TestBahasaUntukPemula:
    def test_penjelasan_tertahan_tanpa_istilah_teknis(
        self, make_context: Callable[..., Any]
    ) -> None:
        kartu = _ringkasan(make_context, records_stuck=500, quarantined_batches=5)

        assert "aman di kapal" in kartu
        assert "dikarantina" not in kartu
        assert "batch" not in kartu.lower()

    @pytest.mark.parametrize(
        "istilah", ["Transport aktif", "Tertua belum terkirim", "Data tertunda"]
    )
    def test_istilah_teknis_tidak_ada_di_ringkasan(
        self, make_context: Callable[..., Any], istilah: str
    ) -> None:
        assert istilah not in _ringkasan(make_context, pending_records=3)

    def test_rincian_tetap_tersedia_lewat_tautan(self, make_context: Callable[..., Any]) -> None:
        kartu = _ringkasan(make_context)

        assert 'href="/sync"' in kartu
        assert "Lihat rincian" in kartu

    def test_halaman_sinkronisasi_tetap_memuat_istilah_teknis(
        self, make_context: Callable[..., Any]
    ) -> None:
        text = _client(make_context()).get("/sync").text

        assert "dikarantina" in text
        assert "Transport" in text


class TestPerkiraanUkuran:
    def test_ukuran_terkirim_dan_belum_terkirim_tampil(
        self, make_context: Callable[..., Any]
    ) -> None:
        kartu = _ringkasan(
            make_context,
            pending_records=8,
            acked_size_bytes=54_637_496,
            unsent_size_bytes=21_516_763,
        )

        assert "sekitar 52.1 MB" in kartu
        assert "sekitar 20.5 MB" in kartu

    def test_satuan_menyesuaikan_dari_byte_sampai_gb(
        self, make_context: Callable[..., Any]
    ) -> None:
        kartu = _ringkasan(
            make_context,
            pending_records=3,
            acked_size_bytes=3 * 1024**3,
            unsent_size_bytes=2_560,
        )

        assert "sekitar 3.0 GB" in kartu
        assert "sekitar 2.5 KB" in kartu

    def test_ukuran_kecil_tampil_dalam_byte(self, make_context: Callable[..., Any]) -> None:
        assert "sekitar 500 B" in _ringkasan(make_context, pending_records=1, unsent_size_bytes=500)

    def test_ukuran_ditandai_perkiraan_bukan_hitungan_pasti(
        self, make_context: Callable[..., Any]
    ) -> None:
        kartu = _ringkasan(make_context, pending_records=1, unsent_size_bytes=1_000_000)

        assert "sekitar" in kartu
        assert "bytes" not in kartu.lower()

    def test_ukuran_tertahan_ikut_dalam_belum_terkirim(
        self, make_context: Callable[..., Any]
    ) -> None:
        kartu = _ringkasan(
            make_context,
            pending_records=8,
            records_stuck=343_294,
            quarantined_batches=4_712,
            unsent_size_bytes=21_516_763,
            stuck_size_bytes=21_516_395,
        )

        assert "sekitar 20.5 MB" in kartu
        assert "343.294 tertahan" in kartu
