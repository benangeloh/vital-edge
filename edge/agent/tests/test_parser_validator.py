"""Test parser dan validator.

Prinsip yang paling penting diuji di sini: pembacaan buruk **ditandai, tidak
dibuang**. Kalau data sensor rusak kita buang, yang terlihat di pusat sama persis
dengan kapal offline — padahal keduanya butuh tindakan yang sangat berbeda.
"""

from __future__ import annotations

import math

import pytest

from fleetview_contracts import Quality
from fleetview_edge.config import SensorConfig, SensorRegistry
from fleetview_edge.parser import TelemetryParser
from fleetview_edge.protocol.base import RawPoint
from fleetview_edge.validator import SensorValidator

US = 1_000_000


def _sensor(**over: object) -> SensorConfig:
    base: dict[str, object] = {"sensor_id": "s1", "channel": "ch1", "metric": "rpm"}
    return SensorConfig(**{**base, **over})  # type: ignore[arg-type]


class TestParser:
    def test_menerapkan_skala(self) -> None:
        reg = SensorRegistry([_sensor(scale=0.1)])
        result = TelemetryParser(reg).parse([RawPoint("ch1", 7425, 1000)])
        assert result.values[0].value == pytest.approx(742.5)

    def test_boolean_tidak_diskalakan(self) -> None:
        """LP-A104 punya 32 input digital; menskalakan boolean tidak bermakna."""
        reg = SensorRegistry([_sensor(scale=0.1, metric="alarm")])
        result = TelemetryParser(reg).parse([RawPoint("ch1", True, 1000)])
        assert result.values[0].value is True

    def test_string_tidak_diskalakan(self) -> None:
        reg = SensorRegistry([_sensor(scale=0.1, metric="barcode")])
        result = TelemetryParser(reg).parse([RawPoint("ch1", "ABC-123", 1000)])
        assert result.values[0].value == "ABC-123"

    @pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
    def test_nilai_non_finite_dihentikan(self, bad: float) -> None:
        """NaN dan infinity harus dihentikan di sini. Kalau lolos, mereka meracuni
        setiap rata-rata dan maksimum di hilir tanpa menimbulkan error apa pun."""
        reg = SensorRegistry([_sensor()])
        result = TelemetryParser(reg).parse([RawPoint("ch1", bad, 1000)])
        assert result.values == []
        assert "ch1" in result.failed

    def test_channel_tak_dikenal_dilaporkan_bukan_error(self) -> None:
        """Perangkat bisa menyajikan lebih banyak channel daripada yang
        dikonfigurasi. Itu informasi, bukan kegagalan."""
        reg = SensorRegistry([_sensor()])
        result = TelemetryParser(reg).parse([RawPoint("ch_asing", 1.0, 1000)])
        assert result.unknown_channels == {"ch_asing"}
        assert result.failed == {}

    def test_satu_channel_menghasilkan_beberapa_nilai(self) -> None:
        reg = SensorRegistry(
            [
                _sensor(sensor_id="a", channel="shared"),
                _sensor(sensor_id="b", channel="shared", scale=2.0),
            ]
        )
        result = TelemetryParser(reg).parse([RawPoint("shared", 10, 1000)])
        assert sorted(v.value for v in result.values) == [10.0, 20.0]  # type: ignore[type-var]

    def test_sensor_nonaktif_diabaikan(self) -> None:
        reg = SensorRegistry([_sensor(enabled=False)])
        result = TelemetryParser(reg).parse([RawPoint("ch1", 1.0, 1000)])
        assert result.values == []
        assert result.unknown_channels == {"ch1"}


class TestValidator:
    def test_nilai_dalam_batas_dianggap_baik(self) -> None:
        s = _sensor(validation={"min_value": 0, "max_value": 2000})
        assert SensorValidator().assess(s, 742.5, US).quality is Quality.GOOD

    @pytest.mark.parametrize("value", [-1.0, 2001.0])
    def test_nilai_di_luar_batas_ditandai_suspect_bukan_dibuang(self, value: float) -> None:
        s = _sensor(validation={"min_value": 0, "max_value": 2000})
        outcome = SensorValidator().assess(s, value, US)
        assert outcome.quality is Quality.SUSPECT
        assert outcome.reason is not None

    def test_tanpa_aturan_selalu_baik(self) -> None:
        assert SensorValidator().assess(_sensor(), 99999.0, US).quality is Quality.GOOD

    def test_laju_perubahan_wajar_diterima(self) -> None:
        s = _sensor(validation={"max_rate_per_second": 800})
        v = SensorValidator()
        v.assess(s, 700.0, US)
        assert v.assess(s, 900.0, 2 * US).quality is Quality.GOOD

    def test_lonjakan_mustahil_ditandai(self) -> None:
        """RPM tidak bisa melompat 700 ke 7000 dalam sedetik. Kalau terjadi,
        kemungkinan besar itu gangguan listrik — bukan mesinnya."""
        s = _sensor(validation={"max_rate_per_second": 800})
        v = SensorValidator()
        v.assess(s, 700.0, US)
        outcome = v.assess(s, 7000.0, 2 * US)
        assert outcome.quality is Quality.SUSPECT
        assert "per detik" in (outcome.reason or "")

    def test_pembacaan_pertama_tidak_bisa_dinilai_lajunya(self) -> None:
        s = _sensor(validation={"max_rate_per_second": 1})
        assert SensorValidator().assess(s, 99999.0, US).quality is Quality.GOOD

    def test_waktu_mundur_tidak_menuduh_sensor(self) -> None:
        """Saat jam melompat mundur, laju perubahan tidak bisa dihitung.
        Menyalahkan sensornya akan salah alamat."""
        s = _sensor(validation={"max_rate_per_second": 1})
        v = SensorValidator()
        v.assess(s, 100.0, 10 * US)
        assert v.assess(s, 5000.0, 5 * US).quality is Quality.GOOD

    def test_boolean_dan_string_selalu_baik(self) -> None:
        """Nilai non-numerik tidak punya konsep rentang maupun laju."""
        s = _sensor(validation={"min_value": 0, "max_value": 1})
        v = SensorValidator()
        assert v.assess(s, True, US).quality is Quality.GOOD
        assert v.assess(s, "ABC", US).quality is Quality.GOOD

    def test_reset_melupakan_riwayat(self) -> None:
        """Dipanggil setelah sambungan pulih, supaya jeda panjang tidak salah
        dibaca sebagai lonjakan laju perubahan."""
        s = _sensor(validation={"max_rate_per_second": 1})
        v = SensorValidator()
        v.assess(s, 100.0, US)
        v.reset()
        assert v.assess(s, 5000.0, 2 * US).quality is Quality.GOOD


class TestStaleness:
    def test_belum_lewat_ambang_bukan_stale(self) -> None:
        s = _sensor(validation={"stale_after_seconds": 10})
        v = SensorValidator()
        v.assess(s, 1.0, 10 * US)
        assert v.check_staleness(s, 15 * US) is None

    def test_melewati_ambang_jadi_stale(self) -> None:
        s = _sensor(validation={"stale_after_seconds": 10})
        v = SensorValidator()
        v.assess(s, 1.0, 10 * US)
        outcome = v.check_staleness(s, 25 * US)
        assert outcome is not None
        assert outcome.quality is Quality.STALE

    def test_nilai_konstan_bukan_stale(self) -> None:
        """Mesin mati memang menghasilkan RPM 0 terus-menerus. Staleness diukur
        dari kapan terakhir TERBACA, bukan kapan terakhir BERUBAH."""
        s = _sensor(validation={"stale_after_seconds": 10})
        v = SensorValidator()
        for i in range(1, 30):
            v.assess(s, 0.0, i * US)
        assert v.check_staleness(s, 29 * US) is None

    def test_tanpa_aturan_tidak_pernah_stale(self) -> None:
        s = _sensor()
        v = SensorValidator()
        v.assess(s, 1.0, US)
        assert v.check_staleness(s, 10_000 * US) is None
