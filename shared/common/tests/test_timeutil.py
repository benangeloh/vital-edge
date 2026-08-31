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
