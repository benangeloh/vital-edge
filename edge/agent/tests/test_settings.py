"""Test config Edge Agent.

Satu build agent dideploy ke 70+ kapal dan hanya config yang membedakan mereka,
jadi pemuatan config adalah salah satu titik paling kritis di sistem ini.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from fleetview_common import ConfigError
from fleetview_edge.settings import EdgeSettings, load_settings

_SHIP = {"ship_id": str(uuid4()), "ship_name": "KM Uji Coba", "device_id": str(uuid4())}


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "edge.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def test_memuat_yaml_minimal(tmp_path: Path) -> None:
    cfg = _write(
        tmp_path,
        f"""
ship:
  ship_id: "{_SHIP["ship_id"]}"
  ship_name: "KM Uji Coba"
  device_id: "{_SHIP["device_id"]}"
""",
    )
    settings = load_settings(cfg)
    assert settings.ship is not None
    assert settings.ship.ship_name == "KM Uji Coba"
    assert settings.console.port == 8080  # default terpakai


def test_env_var_menimpa_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Rahasia dan penyesuaian per-kapal diberikan lewat env, bukan lewat berkas."""
    cfg = _write(
        tmp_path,
        f"""
ship:
  ship_id: "{_SHIP["ship_id"]}"
  ship_name: "Nama Dari Berkas"
  device_id: "{_SHIP["device_id"]}"
console:
  port: 8080
""",
    )
    monkeypatch.setenv("FLEETVIEW_CONSOLE__PORT", "9999")
    assert load_settings(cfg).console.port == 9999


def test_env_var_bersarang_tidak_menghapus_key_lain_di_section_yang_sama(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Menimpa satu key bersarang harus menyisakan sisanya.

    Ini konsekuensi halus dari pembalikan urutan sumber config. Kalau sumber env
    menimpa seluruh section alih-alih menggabungkannya, menyetel satu token
    lewat env akan diam-diam mengembalikan seluruh setelan storage ke default —
    dan retensi 90 hari di kapal berubah tanpa ada yang menyadarinya.
    """
    cfg = _write(
        tmp_path,
        f"""
ship:
  ship_id: "{_SHIP["ship_id"]}"
  ship_name: "KM Uji"
  device_id: "{_SHIP["device_id"]}"
storage:
  influx_bucket: bucket_kustom
  retention_days: 45
""",
    )
    monkeypatch.setenv("FLEETVIEW_STORAGE__INFLUX_TOKEN", "token-dari-env")
    s = load_settings(cfg)

    assert s.storage.influx_token == "token-dari-env"  # env diterapkan
    assert s.storage.influx_bucket == "bucket_kustom"  # YAML tetap utuh
    assert s.storage.retention_days == 45


def test_tanpa_identitas_agent_tetap_start_dalam_mode_setup() -> None:
    """Perangkat baru harus bisa menyala tanpa identitas.

    Edge Console berjalan di dalam proses agent. Kalau agent menolak start tanpa
    identitas, tidak ada antarmuka untuk memasukkannya — teknisi terpaksa
    menyunting YAML lewat SSH, dan itu justru yang ingin dihilangkan.
    """
    settings = load_settings(None)
    assert settings.ship is None
    assert settings.is_configured is False


def test_tanpa_identitas_akuisisi_ditolak() -> None:
    """Perlindungannya tidak hilang, hanya berpindah tempat: agent boleh menyala,
    tetapi tidak boleh mengumpulkan atau menyetor data atas nama kapal yang tidak
    diketahui."""
    settings = load_settings(None)
    with pytest.raises(ConfigError) as exc:
        settings.require_ship()
    assert exc.value.code == "config.not_provisioned"


def test_identitas_setengah_terisi_ditolak() -> None:
    """Kapal yang teridentifikasi separuh lebih berbahaya daripada kapal yang
    jelas-jelas belum dikonfigurasi."""
    from fleetview_edge.settings import ShipIdentity

    with pytest.raises(ValueError):
        ShipIdentity(ship_id=uuid4(), ship_name="KM Uji")  # type: ignore[call-arg]


def test_berkas_config_hilang_memberi_error_jelas(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as exc:
        load_settings(tmp_path / "tidak-ada.yaml")
    assert exc.value.code == "config.file_missing"


def test_yaml_rusak_ditolak(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as exc:
        load_settings(_write(tmp_path, "ship: [belum ditutup"))
    assert exc.value.code == "config.yaml_invalid"


def test_kunci_tak_dikenal_ditolak(tmp_path: Path) -> None:
    """Salah ketik di config kapal terpencil harus ketahuan saat start,
    bukan menjadi setelan yang diam-diam diabaikan."""
    cfg = _write(
        tmp_path,
        f"""
ship:
  ship_id: "{_SHIP["ship_id"]}"
  ship_name: "KM Uji"
  device_id: "{_SHIP["device_id"]}"
consoel:
  port: 8080
""",
    )
    with pytest.raises(ConfigError):
        load_settings(cfg)


def test_log_level_tidak_valid_ditolak(tmp_path: Path) -> None:
    cfg = _write(
        tmp_path,
        f"""
log_level: BUKAN_LEVEL
ship:
  ship_id: "{_SHIP["ship_id"]}"
  ship_name: "KM Uji"
  device_id: "{_SHIP["device_id"]}"
""",
    )
    with pytest.raises(ConfigError):
        load_settings(cfg)


def test_log_level_dinormalisasi_ke_huruf_besar(tmp_path: Path) -> None:
    cfg = _write(
        tmp_path,
        f"""
log_level: debug
ship:
  ship_id: "{_SHIP["ship_id"]}"
  ship_name: "KM Uji"
  device_id: "{_SHIP["device_id"]}"
""",
    )
    assert load_settings(cfg).log_level == "DEBUG"


class TestVerifikasiProduksi:
    """Lebih baik agent menolak jalan daripada berjalan 24/7 di kapal terpencil
    dengan token kosong dan diam-diam tidak pernah berhasil sinkron."""

    def _settings(self, **over: object) -> EdgeSettings:
        return EdgeSettings(environment="production", ship=_SHIP, **over)  # type: ignore[arg-type]

    def test_development_dilewati_tanpa_pemeriksaan(self) -> None:
        EdgeSettings(environment="development", ship=_SHIP).verify_production_ready()

    def test_token_influx_kosong_ditolak(self) -> None:
        with pytest.raises(ConfigError) as exc:
            self._settings().verify_production_ready()
        assert "influx_token" in str(exc.value)

    def test_console_pada_0000_ditolak(self) -> None:
        s = self._settings(
            storage={"influx_token": "t"},
            sync={"central_url": "https://x"},
            collector={"adapter": "lp_a104", "sensors_path": "/etc/fleetview/sensors.yaml"},
            console={"host": "0.0.0.0"},
        )
        with pytest.raises(ConfigError) as exc:
            s.verify_production_ready()
        assert "0.0.0.0" in str(exc.value)

    def test_adapter_simulasi_ditolak_di_produksi(self) -> None:
        """Data simulasi tidak boleh pernah tersaji sebagai data sungguhan.

        Ini penjaga yang paling penting di seluruh pemeriksaan produksi: sebuah
        kapal yang mengirim telemetry simulasi akan tampak sehat sempurna di
        dashboard, dan tidak ada yang akan menyadarinya.
        """
        s = self._settings(
            storage={"influx_token": "t"},
            sync={"central_url": "https://x"},
            collector={"adapter": "simulator", "sensors_path": "/etc/fleetview/sensors.yaml"},
        )
        with pytest.raises(ConfigError) as exc:
            s.verify_production_ready()
        assert "simulator" in str(exc.value)

    def test_sensors_path_wajib_di_produksi(self) -> None:
        s = self._settings(
            storage={"influx_token": "t"},
            sync={"central_url": "https://x"},
            collector={"adapter": "lp_a104"},
        )
        with pytest.raises(ConfigError, match="sensors_path"):
            s.verify_production_ready()

    def test_config_produksi_lengkap_lolos(self) -> None:
        self._settings(
            storage={"influx_token": "t"},
            sync={"central_url": "https://x"},
            collector={"adapter": "lp_a104", "sensors_path": "/etc/fleetview/sensors.yaml"},
        ).verify_production_ready()


class TestConsoleTerbukaKeJaringan:
    """Console tidak punya autentikasi untuk halaman operasionalnya. Membukanya
    ke jaringan kapal harus dinyatakan eksplisit, bukan tercapai diam-diam."""

    def _cfg(self, tmp_path: Path, **console: object) -> Path:
        sensors = tmp_path / "sensors.yaml"
        sensors.write_text("sensors: []\n")
        body = "\n".join(
            f"  {k}: {str(v).lower() if isinstance(v, bool) else v}" for k, v in console.items()
        )
        return _write(
            tmp_path,
            f"""
environment: production
log_format: json
ship:
  ship_id: "{_SHIP["ship_id"]}"
  ship_name: "KM Uji"
  device_id: "{_SHIP["device_id"]}"
collector:
  adapter: lp_a104
  sensors_path: {sensors}
storage:
  influx_token: "t"
sync:
  central_url: "https://pusat.test"
  device_token: "tok"
console:
{body}
""",
        )

    def test_terbuka_tanpa_izin_eksplisit_ditolak(self, tmp_path: Path) -> None:
        settings = load_settings(self._cfg(tmp_path, host="0.0.0.0"))
        with pytest.raises(ConfigError) as exc:
            settings.verify_production_ready()
        assert "allow_lan" in exc.value.message

    def test_terbuka_dengan_izin_eksplisit_diterima(self, tmp_path: Path) -> None:
        settings = load_settings(self._cfg(tmp_path, host="0.0.0.0", allow_lan=True))
        settings.verify_production_ready()

    def test_localhost_tetap_boleh_tanpa_izin(self, tmp_path: Path) -> None:
        settings = load_settings(self._cfg(tmp_path, host="127.0.0.1"))
        settings.verify_production_ready()
