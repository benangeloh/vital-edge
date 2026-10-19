"""Test katalog preset dan penambahan sensor sederhana.

Teknisi cukup memilih jenis sensor dan mengisi alamat sumber di panel. Sisanya
(besaran, satuan, skala, batas wajar, alamat UW tujuan) ditentukan sistem.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fleetview_common import ValidationError
from fleetview_edge.config import load_sensor_registry
from fleetview_edge.config.sensor_editor import add_from_preset, allocate_uw, read_sensors
from fleetview_edge.config.sensor_presets import PRESETS, get_preset


class TestKatalog:
    def test_berisi_tiga_jenis_yang_terdokumentasi(self) -> None:
        assert {p.id for p in PRESETS} >= {"rpm", "fw_temp", "lo_press"}

    def test_tekanan_oli_terverifikasi_dengan_skala_sepersepuluh(self) -> None:
        """31 mentah tampil 3.1 bar di HMI (docs/hardware/konvensi-atlogic.md)."""
        p = get_preset("lo_press")
        assert p.scale == 0.1
        assert p.verified is True

    @pytest.mark.parametrize("preset_id", ["rpm", "fw_temp"])
    def test_skala_yang_belum_dicocokkan_ke_hmi_ditandai(self, preset_id: str) -> None:
        p = get_preset(preset_id)
        assert p.scale == 1.0
        assert p.verified is False
        assert p.note

    def test_id_unik(self) -> None:
        ids = [p.id for p in PRESETS]
        assert len(ids) == len(set(ids))

    def test_jenis_tak_dikenal_ditolak(self) -> None:
        with pytest.raises(ValidationError) as exc:
            get_preset("tidak-ada")
        assert exc.value.code == "sensor.preset_unknown"


def _entries(*channels: str) -> list[dict[str, object]]:
    return [{"sensor_id": f"s{i}", "channel": c} for i, c in enumerate(channels)]


class TestAlokasiUw:
    def test_registry_kosong_mulai_dari_uw45(self) -> None:
        assert allocate_uw([]) == "UW45"

    def test_melewati_alamat_yang_terpakai(self) -> None:
        assert allocate_uw(_entries("UW45", "UW46")) == "UW47"

    def test_mengisi_celah_terendah_lebih_dulu(self) -> None:
        assert allocate_uw(_entries("UW45", "UW47")) == "UW46"

    def test_tidak_peduli_huruf_besar_dan_padding(self) -> None:
        assert allocate_uw(_entries("uw45", "UW00046")) == "UW47"

    def test_mengabaikan_channel_bukan_uw_dan_di_luar_blok(self) -> None:
        assert allocate_uw(_entries("sim:me_port_rpm", "UW100", "UB1000", "UW10")) == "UW45"

    def test_blok_penuh_ditolak_dengan_petunjuk(self) -> None:
        penuh = _entries(*(f"UW{n}" for n in range(45, 81)))
        with pytest.raises(ValidationError) as exc:
            allocate_uw(penuh)
        assert exc.value.code == "sensor.uw_penuh"
        assert "UW100" in exc.value.message


@pytest.fixture
def path(tmp_path: Path) -> Path:
    return tmp_path / "sensors.yaml"


class TestTambahDariPreset:
    def test_lp_a104_alamat_sumber_menghasilkan_uw_otomatis(self, path: Path) -> None:
        add_from_preset(path, adapter="lp_a104", jenis="rpm", sensor_id="me_rpm", alamat="D00160")

        (e,) = read_sensors(path)
        assert e["sensor_id"] == "me_rpm"
        assert e["channel"] == "UW45"
        assert e["panel_source"] == "D00160"
        assert e["metric"] == "rpm"
        assert e["unit"] == "rpm"
        assert e["validation"] == {"min_value": 0.0, "max_value": 2000.0}

    def test_sensor_kedua_mendapat_uw_berikutnya(self, path: Path) -> None:
        add_from_preset(path, adapter="lp_a104", jenis="rpm", sensor_id="a", alamat="D160")
        add_from_preset(path, adapter="lp_a104", jenis="fw_temp", sensor_id="b", alamat="D108")

        assert [e["channel"] for e in read_sensors(path)] == ["UW45", "UW46"]

    def test_skala_preset_diterapkan(self, path: Path) -> None:
        add_from_preset(path, adapter="lp_a104", jenis="lo_press", sensor_id="lo", alamat="D110")

        (e,) = read_sensors(path)
        assert e["scale"] == 0.1
        assert e["metric"] == "pressure"
        assert e["unit"] == "bar"

    def test_alamat_sumber_dinormalkan_lima_digit_huruf_besar(self, path: Path) -> None:
        add_from_preset(path, adapter="lp_a104", jenis="rpm", sensor_id="a", alamat=" d160 ")

        assert read_sensors(path)[0]["panel_source"] == "D00160"

    @pytest.mark.parametrize("alamat", ["", "160", "X123", "D", "D1234567", "UW50"])
    def test_alamat_sumber_tidak_valid_ditolak(self, path: Path, alamat: str) -> None:
        with pytest.raises(ValidationError) as exc:
            add_from_preset(path, adapter="lp_a104", jenis="rpm", sensor_id="a", alamat=alamat)
        assert exc.value.code in {"sensor.alamat_kosong", "sensor.alamat_invalid"}
        assert read_sensors(path) == []

    def test_channel_hasil_pencarian_dipakai_apa_adanya_tanpa_alamat_sumber(
        self, path: Path
    ) -> None:
        """Hasil pencarian sudah berupa alamat UW yang terbaca — baris ladder-nya
        sudah ada di panel, jadi tidak ada alamat D yang perlu dicatat."""
        add_from_preset(path, adapter="lp_a104", jenis="rpm", sensor_id="a", channel="UW52")

        (e,) = read_sensors(path)
        assert e["channel"] == "UW52"
        assert "panel_source" not in e

    def test_channel_di_luar_rentang_manual_ditolak(self, path: Path) -> None:
        with pytest.raises(ValidationError):
            add_from_preset(path, adapter="lp_a104", jenis="rpm", sensor_id="a", channel="UW999999")

    def test_nama_yang_sudah_dipakai_ditolak_bukan_menimpa(self, path: Path) -> None:
        add_from_preset(path, adapter="lp_a104", jenis="rpm", sensor_id="a", alamat="D160")

        with pytest.raises(ValidationError) as exc:
            add_from_preset(path, adapter="lp_a104", jenis="fw_temp", sensor_id="a", alamat="D108")

        assert exc.value.code == "sensor.nama_dipakai"
        (e,) = read_sensors(path)
        assert e["metric"] == "rpm"

    def test_jenis_tak_dikenal_ditolak(self, path: Path) -> None:
        with pytest.raises(ValidationError) as exc:
            add_from_preset(path, adapter="lp_a104", jenis="", sensor_id="a", alamat="D160")
        assert exc.value.code == "sensor.preset_unknown"

    def test_nama_bukan_slug_ditolak(self, path: Path) -> None:
        with pytest.raises(ValidationError):
            add_from_preset(path, adapter="lp_a104", jenis="rpm", sensor_id="Me RPM", alamat="D160")
        assert read_sensors(path) == []

    def test_adapter_lain_memakai_channel_atau_alamat_apa_adanya(self, path: Path) -> None:
        add_from_preset(
            path, adapter="simulator", jenis="rpm", sensor_id="a", channel="sim:me_port_rpm"
        )
        add_from_preset(path, adapter="simulator", jenis="rpm", sensor_id="b", alamat="sim:x")

        assert [e["channel"] for e in read_sensors(path)] == ["sim:me_port_rpm", "sim:x"]
        assert all("panel_source" not in e for e in read_sensors(path))

    def test_adapter_lain_tanpa_alamat_ditolak(self, path: Path) -> None:
        with pytest.raises(ValidationError):
            add_from_preset(path, adapter="simulator", jenis="rpm", sensor_id="a")

    def test_hasilnya_bisa_dimuat_agent(self, path: Path) -> None:
        add_from_preset(path, adapter="lp_a104", jenis="rpm", sensor_id="a", alamat="D160")
        add_from_preset(path, adapter="lp_a104", jenis="lo_press", sensor_id="b", alamat="D110")

        registry = load_sensor_registry(path)

        assert registry.channels == {"UW45", "UW46"}
