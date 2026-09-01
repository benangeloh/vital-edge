"""Test penyuntingan registry sensor lewat Edge Console.

Yang dijaga di sini: teknisi bisa mendaftarkan sensor tanpa menyunting YAML,
dan tidak bisa menuliskan sesuatu yang membuat agent gagal start setelah
restart berikutnya — di kapal, jauh dari orang yang menyuntingnya.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from fleetview_common import ValidationError
from fleetview_edge.config import load_sensor_registry
from fleetview_edge.config.sensor_editor import (
    delete_sensor,
    read_sensors,
    upsert_sensor,
    validate_channel,
)


def _entry(**over: object) -> dict[str, object]:
    return {
        "sensor_id": "me_port_rpm",
        "channel": "UW100",
        "metric": "rpm",
        "unit": "rpm",
        **over,
    }


class TestValidasiAlamat:
    """Alamat divalidasi terhadap yang TERDOKUMENTASI di manual, bukan ditebak."""

    @pytest.mark.parametrize("channel", ["UW0", "UW100", "UW128999", "UB1000", "UB100F"])
    def test_alamat_terdokumentasi_diterima(self, channel: str) -> None:
        assert validate_channel(channel, adapter="lp_a104")

    @pytest.mark.parametrize("channel", ["UW129000", "UW999999"])
    def test_di_luar_rentang_ditolak(self, channel: str) -> None:
        with pytest.raises(ValidationError):
            validate_channel(channel, adapter="lp_a104")

    @pytest.mark.parametrize("channel", ["40001", "%MW100", "rpm", "UX100"])
    def test_bentuk_asing_ditolak_dengan_penjelasan(self, channel: str) -> None:
        """Teknisi mungkin membawa kebiasaan dari PLC lain (mis. alamat Modbus
        40001). Menolaknya diam-diam akan membuat sensor tidak pernah terbaca."""
        with pytest.raises(ValidationError) as exc:
            validate_channel(channel, adapter="lp_a104")
        assert "UW" in exc.value.message

    def test_dinormalkan_ke_bentuk_baku(self) -> None:
        assert validate_channel("uw0100", adapter="lp_a104") == "UW100"

    def test_adapter_simulator_memakai_penamaan_bebas(self) -> None:
        assert validate_channel("sim:me_port_rpm", adapter="simulator") == "sim:me_port_rpm"


class TestMenyimpan:
    def test_sensor_baru_ditulis_dan_bisa_dimuat_agent(self, tmp_path: Path) -> None:
        """Uji yang sesungguhnya menentukan: hasil tulisan harus bisa dibaca
        kembali oleh kode yang sama yang dipakai agent saat start."""
        path = tmp_path / "sensors.yaml"
        upsert_sensor(path, _entry())
        registry = load_sensor_registry(path)
        assert registry.sensor_ids == {"me_port_rpm"}

    def test_menyunting_sensor_yang_ada_tidak_menggandakan(self, tmp_path: Path) -> None:
        path = tmp_path / "sensors.yaml"
        upsert_sensor(path, _entry())
        upsert_sensor(path, _entry(unit="r/min"))
        entries = read_sensors(path)
        assert len(entries) == 1
        assert entries[0]["unit"] == "r/min"

    def test_channel_sama_diizinkan(self, tmp_path: Path) -> None:
        """Satu register perangkat kadang memuat beberapa besaran dengan skala
        berbeda. Melarangnya akan memblokir konfigurasi yang sah."""
        path = tmp_path / "sensors.yaml"
        upsert_sensor(path, _entry())
        upsert_sensor(path, _entry(sensor_id="me_port_rpm_kasar", scale=0.1))
        assert len(read_sensors(path)) == 2

    def test_beberapa_field_satu_sensor_id(self, tmp_path: Path) -> None:
        """Inilah cara GPS mengirim lat/lon sebagai SATU titik — kalau terpisah,
        koordinatnya bisa tercatat separuh."""
        path = tmp_path / "sensors.yaml"
        upsert_sensor(
            path,
            _entry(
                sensor_id="gps", channel="UW200", metric="position", field_name="lat", unit="deg"
            ),
        )
        upsert_sensor(
            path,
            _entry(
                sensor_id="gps", channel="UW202", metric="position", field_name="lon", unit="deg"
            ),
        )
        registry = load_sensor_registry(path)
        assert len(registry.by_id("gps")) == 2

    def test_sensor_id_bukan_slug_ditolak(self, tmp_path: Path) -> None:
        path = tmp_path / "sensors.yaml"
        with pytest.raises(ValidationError) as exc:
            upsert_sensor(path, _entry(sensor_id="RPM Mesin Kiri"))
        assert "sensor_id" in exc.value.message

    def test_entri_tidak_valid_tidak_menyentuh_berkas(self, tmp_path: Path) -> None:
        """Menulis dulu lalu berharap valid akan membuat agent gagal start."""
        path = tmp_path / "sensors.yaml"
        upsert_sensor(path, _entry())
        sebelum = path.read_text()
        with pytest.raises(ValidationError):
            upsert_sensor(path, _entry(sensor_id="sensor_kedua", channel="UW999999"))
        assert path.read_text() == sebelum

    def test_batas_wajar_ikut_tersimpan(self, tmp_path: Path) -> None:
        path = tmp_path / "sensors.yaml"
        upsert_sensor(path, _entry(validation={"min_value": 0, "max_value": 2000}))
        registry = load_sensor_registry(path)
        assert registry.by_id("me_port_rpm")[0].validation.max_value == 2000

    def test_sensor_lain_tidak_terganggu(self, tmp_path: Path) -> None:
        path = tmp_path / "sensors.yaml"
        upsert_sensor(path, _entry())
        upsert_sensor(path, _entry(sensor_id="fuel_level", channel="UW101", metric="level"))
        upsert_sensor(path, _entry(unit="r/min"))
        ids = [e["sensor_id"] for e in read_sensors(path)]
        assert ids == ["me_port_rpm", "fuel_level"]


class TestMenghapus:
    def test_menghapus_menyisakan_yang_lain(self, tmp_path: Path) -> None:
        path = tmp_path / "sensors.yaml"
        upsert_sensor(path, _entry())
        upsert_sensor(path, _entry(sensor_id="fuel_level", channel="UW101", metric="level"))
        assert delete_sensor(path, "me_port_rpm") is True
        assert [e["sensor_id"] for e in read_sensors(path)] == ["fuel_level"]

    def test_menghapus_yang_tidak_ada_bukan_error(self, tmp_path: Path) -> None:
        path = tmp_path / "sensors.yaml"
        upsert_sensor(path, _entry())
        assert delete_sensor(path, "tidak_ada") is False


class TestMembaca:
    def test_entri_rusak_tidak_menyembunyikan_yang_baik(self, tmp_path: Path) -> None:
        """Satu entri rusak tidak boleh membuat seluruh daftar sensor menghilang
        dari layar — teknisi justru membukanya untuk memperbaikinya."""
        path = tmp_path / "sensors.yaml"
        path.write_text(
            yaml.safe_dump(
                {
                    "sensors": [
                        {"sensor_id": "baik", "channel": "UW100", "metric": "rpm"},
                        {"tanpa_sensor_id": True},
                    ]
                }
            )
        )
        entries = read_sensors(path)
        assert len(entries) == 2

    def test_berkas_belum_ada_mengembalikan_kosong(self, tmp_path: Path) -> None:
        assert read_sensors(tmp_path / "belum-ada.yaml") == []


class TestBarisLadder:
    """Jembatan antara dashboard dan panel.

    Nilai sensor hidup di area D milik PLC dan tidak diekspos ke Ethernet, jadi
    tiap sensor menuntut satu baris salin di program panel. Baris itu tidak bisa
    dibuat dari FleetView — yang bisa dilakukan adalah memberitahukannya persis,
    supaya teknisi menyalin alih-alih menebak alamat.
    """

    def test_menyusun_baris_salin(self, tmp_path: Path) -> None:
        from fleetview_edge.config.sensor_editor import ladder_lines

        path = tmp_path / "sensors.yaml"
        upsert_sensor(path, _entry(channel="UW50", panel_source="D00160"))
        hasil = ladder_lines(read_sensors(path))
        assert "MOV   D00160   UW00050" in hasil
        assert "LOAD  M000020" in hasil
        assert "; me_port_rpm" in hasil

    def test_uw_diberi_padding_seperti_atlogic(self, tmp_path: Path) -> None:
        """atLogic menulis UW00050, bukan UW50. Bentuk yang tidak cocok membuat
        teknisi ragu apakah ia menyalin hal yang benar."""
        from fleetview_edge.config.sensor_editor import ladder_lines

        path = tmp_path / "sensors.yaml"
        upsert_sensor(path, _entry(channel="UW7", panel_source="D00108"))
        assert "UW00007" in ladder_lines(read_sensors(path))

    def test_sensor_tanpa_alamat_sumber_disebutkan(self, tmp_path: Path) -> None:
        """Diam-diam melewatinya akan membuat teknisi men-download ladder yang
        kurang satu baris, lalu bingung kenapa satu sensor tetap nol."""
        from fleetview_edge.config.sensor_editor import ladder_lines

        path = tmp_path / "sensors.yaml"
        upsert_sensor(path, _entry(channel="UW50", panel_source="D00160"))
        upsert_sensor(path, _entry(sensor_id="fuel_level", channel="UW51", metric="level"))
        hasil = ladder_lines(read_sensors(path))
        assert "fuel_level" in hasil
        assert "belum diisi" in hasil

    def test_urut_menurut_alamat_tujuan(self, tmp_path: Path) -> None:
        """Blok yang urut mudah dicocokkan dengan layar atLogic saat diperiksa."""
        from fleetview_edge.config.sensor_editor import ladder_lines

        path = tmp_path / "sensors.yaml"
        upsert_sensor(path, _entry(sensor_id="c", channel="UW52", panel_source="D00110"))
        upsert_sensor(path, _entry(sensor_id="a", channel="UW50", panel_source="D00160"))
        hasil = ladder_lines(read_sensors(path))
        assert hasil.index("UW00050") < hasil.index("UW00052")

    def test_kosong_bila_belum_ada_sensor(self) -> None:
        from fleetview_edge.config.sensor_editor import ladder_lines

        assert ladder_lines([]) == ""
