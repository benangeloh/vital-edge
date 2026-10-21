"""Test jalur sensor sederhana di AgentConsoleContext.

Console sendiri hanya meneruskan; yang diuji di sini adalah perekat antara
preset, alokasi alamat, pencarian channel, dan registry di disk.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from fleetview_common import ConfigError
from fleetview_edge.collector import BackoffPolicy, Collector
from fleetview_edge.config import SensorRegistry
from fleetview_edge.console_context import AgentConsoleContext
from fleetview_edge.protocol import SimulatorAdapter
from fleetview_edge.settings import EdgeSettings

_SHIP = {
    "ship_id": "11111111-1111-1111-1111-111111111111",
    "ship_name": "KM Uji Coba",
    "device_id": "22222222-2222-2222-2222-222222222222",
}


def _context(tmp_path: Path, *, adapter: str, collector: Collector | None = None):  # type: ignore[no-untyped-def]
    settings = EdgeSettings(
        ship=_SHIP,
        collector={"adapter": adapter, "sensors_path": str(tmp_path / "sensors.yaml")},
        storage={"data_dir": str(tmp_path)},
    )
    return AgentConsoleContext(settings=settings, agent_version="0.1.0", collector=collector)


async def _sink(_records: object) -> None:
    return None


def _collector() -> Collector:
    return Collector(
        adapter=SimulatorAdapter(seed=1, discover_settle_seconds=0),
        registry=SensorRegistry([]),
        sink=_sink,
        ship_id=UUID(int=1),
        device_id=UUID(int=2),
        backoff=BackoffPolicy(initial_seconds=0.0, max_seconds=0.0, jitter=0.0),
    )


def test_preset_diteruskan_sebagai_dict_untuk_templat(tmp_path: Path) -> None:
    presets = _context(tmp_path, adapter="lp_a104").sensor_presets()

    rpm = next(p for p in presets if p["id"] == "rpm")
    assert rpm["label"] == "RPM mesin"
    assert rpm["verified"] is False


def test_tambah_sederhana_lp_a104_menghasilkan_baris_ladder(tmp_path: Path) -> None:
    ctx = _context(tmp_path, adapter="lp_a104")

    ctx.add_sensor_simple(jenis="rpm", sensor_id="me_rpm", alamat="D160")

    registry = ctx.sensor_registry()
    assert registry["sensors"][0]["channel"] == "UW1000"
    assert "MOV   D00160" in registry["ladder"]
    assert "UW01000" in registry["ladder"]


def test_tambah_sederhana_adapter_simulator_memakai_channel(tmp_path: Path) -> None:
    ctx = _context(tmp_path, adapter="simulator")

    ctx.add_sensor_simple(jenis="rpm", sensor_id="a", channel="sim:me_port_rpm")

    assert ctx.sensor_registry()["sensors"][0]["channel"] == "sim:me_port_rpm"


async def test_cari_tanpa_collector_dinyatakan_jelas(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        await _context(tmp_path, adapter="simulator").discover_channels()


async def test_cari_menyaring_yang_sudah_terdaftar(tmp_path: Path) -> None:
    ctx = _context(tmp_path, adapter="simulator", collector=_collector())
    semua = await ctx.discover_channels()
    assert len(semua) == len(SimulatorAdapter.CHANNELS)

    ctx.add_sensor_simple(jenis="rpm", sensor_id="a", channel="sim:me_port_rpm")
    sisa = await ctx.discover_channels()

    assert "sim:me_port_rpm" not in {h["channel"] for h in sisa}
    assert len(sisa) == len(SimulatorAdapter.CHANNELS) - 1
