"""Test utilitas waktu.

Penolakan datetime naif adalah aturan yang paling penting di sini: menebak zona
waktu akan diam-diam merusak data dari kapal yang jamnya salah setel — persis
kasus yang paling perlu kita deteksi.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from fleetview_common import from_micros, now_micros, now_utc, to_micros


def test_now_utc_selalu_sadar_timezone() -> None:
    assert now_utc().tzinfo is not None


def test_konversi_micros_bolak_balik() -> None:
    original = datetime(2026, 8, 31, 12, 0, 0, 123456, tzinfo=UTC)
    assert from_micros(to_micros(original)) == original


def test_to_micros_menolak_datetime_naif() -> None:
    with pytest.raises(ValueError, match="naif"):
        to_micros(datetime(2026, 8, 31, 12, 0, 0))  # noqa: DTZ001


def test_offset_non_utc_dinormalisasi_dengan_benar() -> None:
    """Kapal beroperasi lintas zona waktu; WIB ke UTC harus akurat."""
    wib = timezone(timedelta(hours=7))
    aware = datetime(2026, 8, 31, 19, 0, 0, tzinfo=wib)
    assert from_micros(to_micros(aware)) == datetime(2026, 8, 31, 12, 0, 0, tzinfo=UTC)


def test_now_micros_masuk_akal() -> None:
    assert from_micros(now_micros()).year >= 2026


class TestZonaWaktuSistemTidakBerpengaruh:
    """Timestamp tidak boleh bergantung pada zona waktu perangkat.

    Kapal berpindah antar zona — Indonesia sendiri melintasi tiga — dan teknisi
    kadang menyetel Pi ke waktu setempat. Kalau itu menggeser data, gejalanya
    baru terlihat berminggu-minggu kemudian sebagai riwayat yang tidak masuk
    akal, dan sangat sulit dilacak balik ke sebabnya.
    """

    @pytest.mark.parametrize("tz", ["UTC", "Asia/Jakarta", "Asia/Jayapura", "America/New_York"])
    def test_epoch_sama_di_zona_mana_pun(self, tz: str, monkeypatch: pytest.MonkeyPatch) -> None:
        import time as _time

        monkeypatch.setenv("TZ", "UTC")
        _time.tzset()
        acuan = now_micros()

        monkeypatch.setenv("TZ", tz)
        _time.tzset()
        diuji = now_micros()

        monkeypatch.setenv("TZ", "UTC")
        _time.tzset()

        # Selisihnya hanya waktu berjalan di antara dua pemanggilan, bukan
        # pergeseran zona (yang akan berupa kelipatan jam).
        assert abs(diuji - acuan) < 5_000_000

    @pytest.mark.parametrize("tz", ["Asia/Jakarta", "Asia/Jayapura"])
    def test_now_utc_tetap_utc(self, tz: str, monkeypatch: pytest.MonkeyPatch) -> None:
        import time as _time

        monkeypatch.setenv("TZ", tz)
        _time.tzset()
        saat = now_utc()
        monkeypatch.setenv("TZ", "UTC")
        _time.tzset()

        assert saat.tzinfo is not None
        assert saat.utcoffset() == timedelta(0)
