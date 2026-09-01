"""Test provisioning lewat Edge Console.

Yang diuji di sini adalah janji utamanya: teknisi cukup memasukkan tiga hal, dan
kredensial yang salah tidak pernah sampai ke disk.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from fleetview_common import ConfigError
from fleetview_edge.provisioning import provision, setup_pin, verify_credentials

SHIP = "11111111-1111-1111-1111-111111111111"
DEVICE = "22222222-2222-2222-2222-222222222222"


def _central(handler: Any) -> Any:
    """Tambal httpx.AsyncClient agar memakai transport tiruan."""
    import fleetview_edge.provisioning as mod

    class _Client(httpx.AsyncClient):
        def __init__(self, **kw: Any) -> None:
            kw.pop("timeout", None)
            super().__init__(transport=httpx.MockTransport(handler), **kw)

    return mod, _Client


def _ok(_r: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "ok": True,
            "data": {
                "access_token": "t",
                "device_id": DEVICE,
                "ship_id": SHIP,
                "ship_name": "KM Sinar Jaya",
                "ship_slug": "SHIP-071",
            },
            "error": None,
            "meta": {},
        },
    )


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch):
    def _apply(handler: Any) -> None:
        mod, client = _central(handler)
        monkeypatch.setattr(mod.httpx, "AsyncClient", client)

    return _apply


class TestVerifikasi:
    async def test_identitas_diturunkan_bukan_diketik(self, patched) -> None:
        """Inilah alasan fitur ini ada: UUID yang diketik ulang adalah sumber
        salah ketik yang baru ketahuan setelah data masuk atas nama kapal lain."""
        patched(_ok)
        hs = await verify_credentials(
            central_url="http://central.test", client_id="ship-071", secret="rahasia"
        )
        assert hs.ship_id == SHIP
        assert hs.device_id == DEVICE
        assert hs.ship_name == "KM Sinar Jaya"

    async def test_kredensial_ditolak_dijelaskan(self, patched) -> None:
        patched(lambda _r: httpx.Response(401, json={"error": "no"}))
        with pytest.raises(ConfigError) as exc:
            await verify_credentials(central_url="http://central.test", client_id="x", secret="y")
        assert exc.value.code == "provisioning.rejected"
        assert "client_id" in exc.value.message

    async def test_central_tak_terjangkau_dibedakan_dari_ditolak(self, patched) -> None:
        """Dua keadaan yang penanganannya berlawanan: yang satu tunggu jaringan,
        yang satu perbaiki kredensial."""

        def boom(_r: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("tidak ada rute")

        patched(boom)
        with pytest.raises(ConfigError) as exc:
            await verify_credentials(central_url="http://central.test", client_id="x", secret="y")
        assert exc.value.code == "provisioning.unreachable"

    async def test_central_versi_lama_dilaporkan_jelas(self, patched) -> None:
        patched(lambda _r: httpx.Response(200, json={"data": {"access_token": "t"}}))
        with pytest.raises(ConfigError) as exc:
            await verify_credentials(central_url="http://central.test", client_id="x", secret="y")
        assert exc.value.code == "provisioning.incomplete_response"


class TestPenulisan:
    async def test_config_dan_secrets_ditulis(self, patched, tmp_path: Path) -> None:
        patched(_ok)
        result = await provision(
            central_url="http://central.test",
            client_id="ship-071",
            secret="rahasia-panjang",
            config_path=tmp_path / "edge.yaml",
            secrets_path=tmp_path / "secrets.env",
        )
        cfg = yaml.safe_load(result.config_path.read_text())
        assert cfg["ship"] == {
            "ship_id": SHIP,
            "ship_name": "KM Sinar Jaya",
            "device_id": DEVICE,
        }
        assert cfg["sync"]["central_url"] == "http://central.test"
        assert "rahasia-panjang" in result.secrets_path.read_text()

    async def test_rahasia_tidak_masuk_ke_config(self, patched, tmp_path: Path) -> None:
        """edge.yaml sering dibuka, disalin, dan dilampirkan saat diagnosis."""
        patched(_ok)
        result = await provision(
            central_url="http://central.test",
            client_id="ship-071",
            secret="rahasia-panjang",
            config_path=tmp_path / "edge.yaml",
            secrets_path=tmp_path / "secrets.env",
        )
        assert "rahasia-panjang" not in result.config_path.read_text()

    async def test_secrets_hanya_bisa_dibaca_pemiliknya(self, patched, tmp_path: Path) -> None:
        patched(_ok)
        result = await provision(
            central_url="http://central.test",
            client_id="a",
            secret="b",
            config_path=tmp_path / "edge.yaml",
            secrets_path=tmp_path / "secrets.env",
        )
        assert oct(result.secrets_path.stat().st_mode)[-3:] == "600"

    async def test_kredensial_salah_tidak_menulis_apa_pun(self, patched, tmp_path: Path) -> None:
        """Verifikasi dulu, tulis belakangan. Menulis lalu berharap benar akan
        meninggalkan perangkat setengah terkonfigurasi yang gagal diam-diam."""
        patched(lambda _r: httpx.Response(401, json={}))
        with pytest.raises(ConfigError):
            await provision(
                central_url="http://central.test",
                client_id="x",
                secret="y",
                config_path=tmp_path / "edge.yaml",
                secrets_path=tmp_path / "secrets.env",
            )
        assert not (tmp_path / "edge.yaml").exists()
        assert not (tmp_path / "secrets.env").exists()

    async def test_setelan_lain_tidak_hilang(self, patched, tmp_path: Path) -> None:
        """Pemetaan sensor dan tuning adalah hasil kerja orang lain; provisioning
        ulang tidak boleh menghapusnya."""
        cfg_path = tmp_path / "edge.yaml"
        cfg_path.write_text(
            yaml.safe_dump(
                {
                    "environment": "production",
                    "collector": {"adapter": "lp_a104", "poll_interval_seconds": 2.0},
                    "storage": {"retention_days": 120},
                    "sync": {"batch_max_records": 250},
                }
            )
        )
        patched(_ok)
        await provision(
            central_url="http://central.test",
            client_id="a",
            secret="b",
            config_path=cfg_path,
            secrets_path=tmp_path / "secrets.env",
        )
        cfg = yaml.safe_load(cfg_path.read_text())
        assert cfg["environment"] == "production"
        assert cfg["collector"]["poll_interval_seconds"] == 2.0
        assert cfg["storage"]["retention_days"] == 120
        assert cfg["sync"]["batch_max_records"] == 250  # tuning bertahan
        assert cfg["sync"]["central_url"] == "http://central.test"  # yang baru masuk

    async def test_hasil_provisioning_bisa_dimuat_agent(self, patched, tmp_path: Path) -> None:
        """Uji yang sesungguhnya menentukan: config yang ditulis harus benar-benar
        bisa dibaca kembali oleh agent."""
        from fleetview_edge.settings import load_settings

        patched(_ok)
        cfg_path = tmp_path / "edge.yaml"
        await provision(
            central_url="http://central.test",
            client_id="ship-071",
            secret="rahasia",
            config_path=cfg_path,
            secrets_path=tmp_path / "secrets.env",
        )
        settings = load_settings(cfg_path)
        assert settings.ship is not None
        assert str(settings.ship.ship_id) == SHIP
        assert settings.ship.ship_name == "KM Sinar Jaya"


class TestPin:
    def test_pin_bertahan_antar_pemanggilan(self, tmp_path: Path) -> None:
        path = tmp_path / "setup.pin"
        first = setup_pin(path)
        assert first == setup_pin(path), "PIN berubah akan mengunci teknisi di tengah setup"

    def test_pin_enam_digit_dan_rahasia(self, tmp_path: Path) -> None:
        path = tmp_path / "setup.pin"
        pin = setup_pin(path)
        assert len(pin) == 6 and pin.isdigit()
        assert oct(path.stat().st_mode)[-3:] == "600"
