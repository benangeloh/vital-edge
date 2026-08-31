"""Test konfigurasi sensor."""

from __future__ import annotations

from pathlib import Path

import pytest

from fleetview_common import ConfigError
from fleetview_edge.config import SensorConfig, SensorRegistry, load_sensor_registry

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE = REPO_ROOT / "edge" / "agent" / "config" / "sensors.example.yaml"


def _sensor(**over: object) -> SensorConfig:
    base: dict[str, object] = {"sensor_id": "s1", "channel": "ch1", "metric": "rpm"}
    return SensorConfig(**{**base, **over})


class TestSensorConfig:
    def test_skala_dan_offset(self) -> None:
        """Perangkat industri lazim mengirim bilangan bulat berskala."""
        s = _sensor(scale=0.1)
        assert s.apply_scaling(7425) == pytest.approx(742.5)

    def test_offset_diterapkan_setelah_skala(self) -> None:
        assert _sensor(scale=2.0, offset=10.0).apply_scaling(5) == pytest.approx(20.0)

    @pytest.mark.parametrize("bad", ["Sensor1", "sensor-1", "sensor 1", "sensör", ""])
    def test_sensor_id_harus_slug(self, bad: str) -> None:
        with pytest.raises(Exception, match=r"slug|String should"):
            _sensor(sensor_id=bad)

    @pytest.mark.parametrize("ok", ["1sensor", "me_port_rpm", "gps2", "a"])
    def test_slug_yang_sah(self, ok: str) -> None:
        """Diawali angka itu sah — pola sengaja mengizinkannya, karena penamaan
        sensor di lapangan sering memakai nomor tag seperti `1ta301`."""
        assert _sensor(sensor_id=ok).sensor_id == ok

    def test_metric_baru_diterima(self) -> None:
        """Sensor bisa bertambah tanpa perubahan kode."""
        assert _sensor(metric="shaft_torque").metric == "shaft_torque"

    def test_tag_milik_sistem_ditolak(self) -> None:
        with pytest.raises(Exception, match="disediakan sistem"):
            _sensor(tags={"ship_id": "x"})

    def test_batas_validasi_terbalik_ditolak(self) -> None:
        with pytest.raises(Exception, match="melebihi"):
            _sensor(validation={"min_value": 100, "max_value": 0})

    def test_kunci_tak_dikenal_ditolak(self) -> None:
        """Salah ketik di config kapal terpencil harus ketahuan saat start."""
        with pytest.raises(Exception, match="Extra inputs"):
            _sensor(pol_interval_seconds=1.0)


class TestSensorRegistry:
    def test_beberapa_sinyal_berbagi_sensor_id_untuk_multi_field(self) -> None:
        """GPS: lat/lon/sog/cog jadi satu record supaya koordinat tak terpisah."""
        reg = SensorRegistry(
            [
                _sensor(sensor_id="gps", channel="c_lat", metric="position", field_name="lat"),
                _sensor(sensor_id="gps", channel="c_lon", metric="position", field_name="lon"),
            ]
        )
        assert len(reg.by_id("gps")) == 2
        assert reg.sensor_ids == {"gps"}

    def test_sinyal_ganda_ditolak(self) -> None:
        with pytest.raises(ConfigError) as exc:
            SensorRegistry([_sensor(), _sensor(channel="ch2")])
        assert exc.value.code == "config.duplicate_signal"

    def test_sensor_id_dengan_metric_berbeda_ditolak(self) -> None:
        """Entri yang berbagi sensor_id membentuk satu record; kalau metric-nya
        berbeda, isi record jadi bergantung urutan pembacaan — bug yang sangat
        sulit dilacak."""
        with pytest.raises(ConfigError) as exc:
            SensorRegistry(
                [
                    _sensor(sensor_id="gps", channel="a", metric="position", field_name="lat"),
                    _sensor(sensor_id="gps", channel="b", metric="gps", field_name="lon"),
                ]
            )
        assert exc.value.code == "config.inconsistent_signal_group"

    def test_satu_channel_bisa_melayani_beberapa_sensor(self) -> None:
        reg = SensorRegistry(
            [
                _sensor(sensor_id="a", channel="shared"),
                _sensor(sensor_id="b", channel="shared", scale=0.5),
            ]
        )
        assert len(reg.for_channel("shared")) == 2

    def test_sensor_nonaktif_dikecualikan(self) -> None:
        reg = SensorRegistry(
            [_sensor(sensor_id="a"), _sensor(sensor_id="b", channel="c2", enabled=False)]
        )
        assert len(reg.enabled) == 1
        assert reg.channels == {"ch1"}

    def test_channel_tak_dikenal_mengembalikan_kosong(self) -> None:
        assert SensorRegistry([_sensor()]).for_channel("tidak_ada") == []


class TestPemuatanYaml:
    def test_contoh_config_valid(self) -> None:
        """Berkas contoh harus selalu bisa dimuat — ia jadi acuan operator."""
        reg = load_sensor_registry(EXAMPLE)
        assert len(reg) > 0
        assert "gps_main" in reg.sensor_ids
        assert len(reg.by_id("gps_main")) == 4  # lat, lon, sog, cog

    def test_berkas_hilang_memberi_error_jelas(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError) as exc:
            load_sensor_registry(tmp_path / "tidak-ada.yaml")
        assert exc.value.code == "config.sensors_missing"

    def test_yaml_rusak_ditolak(self, tmp_path: Path) -> None:
        p = tmp_path / "s.yaml"
        p.write_text("sensors: [belum ditutup", encoding="utf-8")
        with pytest.raises(ConfigError) as exc:
            load_sensor_registry(p)
        assert exc.value.code == "config.yaml_invalid"

    def test_kunci_sensors_wajib_ada(self, tmp_path: Path) -> None:
        p = tmp_path / "s.yaml"
        p.write_text("bukan_sensors: []", encoding="utf-8")
        with pytest.raises(ConfigError) as exc:
            load_sensor_registry(p)
        assert exc.value.code == "config.sensors_invalid"
