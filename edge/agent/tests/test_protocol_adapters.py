"""Test MockLPAAdapter dan SimulatorAdapter.

Fokusnya pada kegagalan yang bisa diskenariokan — menangani putusnya sambungan,
timeout, dan kegagalan sebagian adalah persyaratan Phase 2, dan hal-hal itu
mustahil diuji dengan andal kalau hanya menunggu kebetulan.
"""

from __future__ import annotations

import pytest

from fleetview_common import ProtocolError
from fleetview_edge.protocol import (
    EnginePhase,
    LinkState,
    LPA104Adapter,
    MockLPAAdapter,
    MockScript,
    ProtocolNotConnectedError,
    ProtocolTimeoutError,
    SimulatorAdapter,
)


class TestMockAdapter:
    async def test_mengembalikan_nilai_yang_dikonfigurasi(self) -> None:
        adapter = MockLPAAdapter({"a": 1.0, "b": 2.0})
        await adapter.connect()
        points = await adapter.read_points()
        assert {p.channel: p.value for p in points} == {"a": 1.0, "b": 2.0}

    async def test_membaca_sebelum_connect_ditolak(self) -> None:
        with pytest.raises(ProtocolNotConnectedError):
            await MockLPAAdapter({"a": 1.0}).read_points()

    async def test_timeout_bisa_diskenariokan(self) -> None:
        adapter = MockLPAAdapter({"a": 1.0}, script=MockScript(timeout_on=[1]))
        await adapter.connect()
        await adapter.read_points()  # putaran 0 berhasil
        with pytest.raises(ProtocolTimeoutError):
            await adapter.read_points()  # putaran 1 timeout

    async def test_putus_sambungan_bisa_diskenariokan(self) -> None:
        adapter = MockLPAAdapter({"a": 1.0}, script=MockScript(disconnect_on=[0]))
        await adapter.connect()
        with pytest.raises(ProtocolNotConnectedError):
            await adapter.read_points()
        assert adapter.is_connected is False

    async def test_kegagalan_sebagian_menyisakan_channel_lain(self) -> None:
        """Satu sensor rusak tidak boleh membutakan seluruh kapal."""
        adapter = MockLPAAdapter(
            {"a": 1.0, "b": 2.0, "c": 3.0},
            script=MockScript(fail_channels_on={0: ["b"]}),
        )
        await adapter.connect()
        points = await adapter.read_points()
        assert {p.channel for p in points} == {"a", "c"}

    async def test_connect_gagal_beberapa_kali_lalu_berhasil(self) -> None:
        adapter = MockLPAAdapter({"a": 1.0}, script=MockScript(connect_failures=2))
        for _ in range(2):
            with pytest.raises(ProtocolNotConnectedError):
                await adapter.connect()
        await adapter.connect()
        assert adapter.is_connected is True
        assert adapter.connect_attempts == 3

    async def test_health_mencerminkan_kondisi(self) -> None:
        adapter = MockLPAAdapter({"a": 1.0})
        assert (await adapter.health()).link is LinkState.DISCONNECTED
        await adapter.connect()
        assert (await adapter.health()).link is LinkState.CONNECTED
        assert (await adapter.health()).healthy is True

    async def test_context_manager_menyambung_dan_menutup(self) -> None:
        adapter = MockLPAAdapter({"a": 1.0})
        async with adapter:
            assert adapter.is_connected is True
        assert adapter.is_connected is False


class TestSimulator:
    async def test_menghasilkan_semua_channel(self) -> None:
        sim = SimulatorAdapter(seed=42)
        await sim.connect()
        points = await sim.read_points()
        assert {p.channel for p in points} == set(SimulatorAdapter.CHANNELS)

    async def test_deterministik_dengan_seed_yang_sama(self) -> None:
        """Uji beban dan test butuh hasil yang bisa diulang."""

        async def run() -> list[float]:
            sim = SimulatorAdapter(seed=7)
            await sim.connect()
            return [float(p.value) for p in await sim.read_points()]

        assert await run() == await run()

    async def test_mesin_mati_menghasilkan_rpm_nol(self) -> None:
        sim = SimulatorAdapter(phase=EnginePhase.STOPPED, seed=1, noise=0.0)
        await sim.connect()
        values = {p.channel: p.value for p in await sim.read_points()}
        assert values["sim:me_port_rpm"] == 0.0
        assert values["sim:fuel_flow"] == 0.0

    async def test_cruise_menghasilkan_rpm_masuk_akal(self) -> None:
        sim = SimulatorAdapter(phase=EnginePhase.CRUISE, seed=1)
        await sim.connect()
        values = {p.channel: p.value for p in await sim.read_points()}
        assert 1200 < float(values["sim:me_port_rpm"]) < 1700

    async def test_bahan_bakar_berkurang_saat_mesin_bekerja(self) -> None:
        sim = SimulatorAdapter(phase=EnginePhase.CRUISE, seed=3, noise=0.0)
        await sim.connect()
        first = {p.channel: p.value for p in await sim.read_points()}["sim:fuel_level"]
        for _ in range(50):
            await sim.read_points()
        last = {p.channel: p.value for p in await sim.read_points()}["sim:fuel_level"]
        assert float(last) < float(first)


class TestLPA104AdapterKerangka:
    """Adapter sungguhan harus GAGAL KERAS, bukan diam-diam mengembalikan kosong.

    Adapter yang mengembalikan daftar kosong akan tampak persis seperti kapal
    yang sensornya mati — kondisi yang paling tidak boleh disamarkan.
    """

    async def test_host_kosong_ditolak_saat_dibuat(self) -> None:
        """Panel tanpa alamat tidak punya perilaku yang masuk akal. Ditolak saat
        dibuat, bukan saat polling pertama gagal di tengah laut."""
        with pytest.raises(ProtocolError, match="host"):
            LPA104Adapter(host="")

    async def test_channel_kosong_bukan_pembacaan_sukses(self) -> None:
        """Registry kosong akan membuat kapal terlihat persis seperti kapal yang
        sehat tetapi sensornya mati — keadaan yang paling tidak boleh disamarkan."""
        adapter = LPA104Adapter(host="127.0.0.1", channels=[])
        with pytest.raises(ProtocolError, match="tidak ada channel"):
            await adapter.read_points()

    @pytest.mark.parametrize("channel", ["UW50", "uw0050", "UW128999"])
    def test_channel_uw_diurai_jadi_nomor_register(self, channel: str) -> None:
        """UW000nn <-> register Modbus nn, dipastikan lewat rung 26 ladder
        (`MOV H0002 UW00020`) yang memang terbaca 2 di register 20."""
        from fleetview_edge.protocol.lp_a104.adapter import parse_uw_channel

        assert parse_uw_channel(channel) == int(channel[2:])

    @pytest.mark.parametrize("channel", ["40001", "D160", "UW999999"])
    def test_channel_asing_ditolak(self, channel: str) -> None:
        """D160 khususnya: itu alamat area D milik PLC, BUKAN alamat Modbus.
        Membacanya lewat Ethernet selalu mengembalikan nol."""
        from fleetview_edge.protocol.lp_a104.adapter import parse_uw_channel

        with pytest.raises(ProtocolError):
            parse_uw_channel(channel)

    async def test_health_tetap_menjawab_sebelum_terhubung(self) -> None:
        """health() tidak boleh melempar, justru saat keadaan buruk."""
        health = await LPA104Adapter(host="192.168.100.101", channels=["UW50"]).health()
        assert health.link is LinkState.DISCONNECTED
        assert health.detail is not None and "192.168.100.101" in health.detail
