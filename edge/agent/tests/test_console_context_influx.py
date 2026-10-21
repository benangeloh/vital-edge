"""Rincian InfluxDB lokal yang ditampilkan Console: dari mana kata sandi diambil."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fleetview_edge.console_context import AgentConsoleContext
from fleetview_edge.settings import EdgeSettings

_SHIP = {
    "ship_id": "11111111-1111-1111-1111-111111111111",
    "ship_name": "KM Uji Coba",
    "device_id": "22222222-2222-2222-2222-222222222222",
}


def _context(
    tmp_path: Path, *, environment: str, sandi_file: str | None = None, **storage: Any
) -> AgentConsoleContext:
    config_path = tmp_path / "config" / "edge.yaml"
    config_path.parent.mkdir()
    if sandi_file is not None:
        (config_path.parent / "influx-admin.password").write_text(sandi_file + "\n")
    settings = EdgeSettings(
        environment=environment,  # type: ignore[arg-type]
        ship=_SHIP,
        storage={"data_dir": str(tmp_path), **storage},
        config_path=config_path,
    )
    return AgentConsoleContext(settings=settings, agent_version="0.1.0")


async def test_sandi_dari_berkas_perangkat_tertulis_apa_adanya_di_produksi(
    tmp_path: Path,
) -> None:
    """Dipasang `install.sh` dengan sandi bawaan yang mudah; teknisi di kapal
    cukup membacanya di halaman Log, tanpa PIN dan tanpa terminal."""
    detail = await _context(
        tmp_path, environment="production", sandi_file="fleetview123"
    ).storage_detail()
    assert detail["sandi_terbuka"] == "fleetview123"


async def test_berkas_didahulukan_atas_config(tmp_path: Path) -> None:
    detail = await _context(
        tmp_path,
        environment="development",
        sandi_file="dari-berkas",
        influx_password="dari-config",
    ).storage_detail()
    assert detail["sandi_terbuka"] == "dari-berkas"


async def test_development_tanpa_berkas_memakai_config(tmp_path: Path) -> None:
    detail = await _context(
        tmp_path, environment="development", influx_password="dev-pass"
    ).storage_detail()
    assert detail["sandi_terbuka"] == "dev-pass"


async def test_tanpa_berkas_dan_tanpa_config_tidak_menampilkan_apa_apa(tmp_path: Path) -> None:
    detail = await _context(tmp_path, environment="development").storage_detail()
    assert detail["sandi_terbuka"] is None


async def test_sandi_di_config_produksi_diabaikan(tmp_path: Path) -> None:
    """Di produksi sumbernya hanya berkas yang ditulis `install.sh`; YAML tidak."""
    detail = await _context(
        tmp_path, environment="production", influx_password="dari-config"
    ).storage_detail()
    assert detail["sandi_terbuka"] is None
