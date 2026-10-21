"""Test pencarian channel yang aktif di perangkat.

Pencarian hanya bisa menemukan register yang SUDAH berisi nilai. Sensor yang
masih bernilai 0 (mis. mesin mati) tidak terlihat, dan arti sebuah register
tidak pernah ditebak: teknisi yang menamainya.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

from fleetview_common import ProtocolError
from fleetview_edge.collector import BackoffPolicy, Collector
from fleetview_edge.config import SensorRegistry
from fleetview_edge.config.sensor_discovery import annotate
from fleetview_edge.protocol import LPA104Adapter, MockLPAAdapter, SimulatorAdapter
from fleetview_edge.protocol.base import DiscoveredChannel

NO_WAIT = BackoffPolicy(initial_seconds=0.0, max_seconds=0.0, jitter=0.0)


class TestAdapterTanpaDukungan:
    async def test_adapter_bawaan_menolak_dengan_kode_jelas(self) -> None:
        with pytest.raises(ProtocolError) as exc:
            await MockLPAAdapter({}).discover()
        assert exc.value.code == "protocol.discovery_unsupported"
        assert exc.value.retryable is False


class TestSimulator:
    async def test_menyajikan_semua_channel_simulasi(self) -> None:
        sim = SimulatorAdapter(seed=1, discover_settle_seconds=0)
        await sim.connect()

        found = await sim.discover()

        assert {f.channel for f in found} == set(SimulatorAdapter.CHANNELS)
        assert all(isinstance(f.changing, bool) for f in found)

    async def test_belum_tersambung_ditolak(self) -> None:
        sim = SimulatorAdapter(discover_settle_seconds=0)
        with pytest.raises(ProtocolError):
            await sim.discover()


class _KlienTiruan:
    """Mengembalikan snapshot berurutan untuk read_range, dan mencatat permintaannya."""

    def __init__(self, *snapshots: dict[int, int]) -> None:
        self._snapshots = list(snapshots)
        self.requests: list[tuple[int, int]] = []

    async def read_range(self, address: int, count: int) -> list[int]:
        self.requests.append((address, count))
        isi = self._snapshots.pop(0)
        return [isi.get(address + i, 0) for i in range(count)]


def _lp(klien: _KlienTiruan) -> LPA104Adapter:
    adapter = LPA104Adapter(host="panel.test", discover_settle_seconds=0)
    adapter._client = klien  # type: ignore[assignment]
    return adapter


class TestLpA104:
    async def test_hanya_register_berisi_yang_dilaporkan(self) -> None:
        klien = _KlienTiruan({1000: 2, 1003: 1500, 1004: 70}, {1000: 2, 1003: 1510, 1004: 70})

        found = await _lp(klien).discover()

        assert [(f.channel, f.value, f.changing) for f in found] == [
            ("UW1000", 2, False),
            ("UW1003", 1510, True),
            ("UW1004", 70, False),
        ]

    async def test_register_nol_di_kedua_pembacaan_tidak_muncul(self) -> None:
        found = await _lp(_KlienTiruan({}, {})).discover()

        assert found == []

    async def test_register_yang_baru_terisi_di_pembacaan_kedua_muncul(self) -> None:
        found = await _lp(_KlienTiruan({}, {1060: 5})).discover()

        assert [f.channel for f in found] == ["UW1060"]
        assert found[0].changing is True

    async def test_hanya_membaca_area_terdokumentasi_dalam_satu_permintaan_per_pembacaan(
        self,
    ) -> None:
        """UW1000-UW1099 (100 word) muat dalam satu permintaan, di bawah batas 123."""
        klien = _KlienTiruan({}, {})

        await _lp(klien).discover()

        assert klien.requests == [(1000, 100), (1000, 100)]

    async def test_tidak_butuh_channel_terdaftar(self) -> None:
        adapter = LPA104Adapter(host="panel.test", channels=[], discover_settle_seconds=0)
        adapter._client = _KlienTiruan({1003: 1}, {1003: 1})  # type: ignore[assignment]

        assert [f.channel for f in await adapter.discover()] == ["UW1003"]

    async def test_kegagalan_baca_diteruskan(self) -> None:
        class _Gagal:
            async def read_range(self, address: int, count: int) -> list[int]:
                raise ProtocolError("panel tidak membalas", code="modbus.no_reply")

        adapter = LPA104Adapter(host="panel.test", discover_settle_seconds=0)
        adapter._client = _Gagal()  # type: ignore[assignment]

        with pytest.raises(ProtocolError) as exc:
            await adapter.discover()
        assert exc.value.code == "modbus.no_reply"


class TestCollector:
    async def test_menyambung_perangkat_bila_belum_dan_registry_boleh_kosong(self) -> None:
        sim = SimulatorAdapter(seed=1, discover_settle_seconds=0)
        collector = Collector(
            adapter=sim,
            registry=SensorRegistry([]),
            sink=_sink,
            ship_id=UUID(int=1),
            device_id=UUID(int=2),
            backoff=NO_WAIT,
        )

        found = await collector.discover()

        assert len(found) == len(SimulatorAdapter.CHANNELS)

    async def test_tak_bisa_menyambung_dilaporkan_sebagai_protocol_error(self) -> None:
        class _TakBisa(MockLPAAdapter):
            async def connect(self) -> None:
                raise ProtocolError("tidak bisa menyambung", code="modbus.connect_failed")

        collector = Collector(
            adapter=_TakBisa({}),
            registry=SensorRegistry([]),
            sink=_sink,
            ship_id=UUID(int=1),
            device_id=UUID(int=2),
            backoff=NO_WAIT,
        )

        with pytest.raises(ProtocolError):
            await collector.discover()


async def _sink(_records: Any) -> None:
    return None


def _d(channel: str, value: int = 1, changing: bool = False) -> DiscoveredChannel:
    return DiscoveredChannel(channel=channel, value=value, changing=changing)


class TestAnnotate:
    def test_yang_sudah_terdaftar_disembunyikan_walau_beda_penulisan(self) -> None:
        entries = [{"sensor_id": "a", "channel": "UW50"}]

        hasil = annotate([_d("UW50"), _d("UW51")], entries, adapter="lp_a104")

        assert [h["channel"] for h in hasil] == ["UW51"]

    def test_padding_dan_huruf_besar_diabaikan_saat_mencocokkan(self) -> None:
        entries = [{"sensor_id": "a", "channel": "uw00050"}]

        assert annotate([_d("UW50")], entries, adapter="lp_a104") == []

    def test_blok_sensor_ditandai_dan_diurutkan_lebih_dulu(self) -> None:
        hasil = annotate([_d("UW20"), _d("UW1060"), _d("UW1000")], [], adapter="lp_a104")

        assert [(h["channel"], h["likely_sensor"]) for h in hasil] == [
            ("UW1000", True),
            ("UW1060", True),
            ("UW20", False),
        ]

    def test_batas_blok_uw1000_sampai_uw1099(self) -> None:
        hasil = annotate(
            [_d("UW999"), _d("UW1000"), _d("UW1099"), _d("UW1100")], [], adapter="lp_a104"
        )

        assert {h["channel"]: h["likely_sensor"] for h in hasil} == {
            "UW999": False,
            "UW1000": True,
            "UW1099": True,
            "UW1100": False,
        }

    def test_adapter_lain_semuanya_kemungkinan_sensor(self) -> None:
        hasil = annotate([_d("sim:fuel_flow")], [], adapter="simulator")

        assert hasil[0]["likely_sensor"] is True

    def test_nilai_dan_status_berubah_diteruskan(self) -> None:
        (h,) = annotate([_d("UW50", 1510, True)], [], adapter="lp_a104")

        assert (h["value"], h["changing"]) == (1510, True)
