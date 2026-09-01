"""Test klien Modbus TCP terhadap panel tiruan.

Batas-batas yang diuji di sini bukan dari spesifikasi Modbus, melainkan hasil
pengukuran langsung terhadap panel LP-A104 sungguhan pada 2026-08-21. Keduanya
merusak dengan cara yang sulit didiagnosis, jadi keduanya dikunci di sini.
"""

from __future__ import annotations

import asyncio
import struct

import pytest

from fleetview_common import ProtocolError
from fleetview_edge.protocol.lp_a104.modbus import (
    MAX_REGISTERS_PER_REQUEST,
    ModbusTcpClient,
)


class PanelTiruan:
    """Server Modbus TCP minimal yang meniru perilaku panel."""

    def __init__(self, nilai: dict[int, int] | None = None) -> None:
        self.nilai = nilai or {}
        self.permintaan: list[tuple[int, int]] = []
        self.koneksi = 0
        self._server: asyncio.AbstractServer | None = None
        self.port = 0

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._layani, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _layani(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.koneksi += 1
        try:
            while True:
                header = await reader.readexactly(7)
                tid, _proto, panjang, _unit = struct.unpack(">HHHB", header)
                pdu = await reader.readexactly(panjang - 1)
                _fc, alamat, jumlah = struct.unpack(">BHH", pdu)
                self.permintaan.append((alamat, jumlah))
                data = b"".join(
                    struct.pack(">H", self.nilai.get(alamat + i, 0)) for i in range(jumlah)
                )
                badan = struct.pack(">BB", 3, len(data)) + data
                writer.write(struct.pack(">HHH", tid, 0, len(badan) + 1) + b"\x01" + badan)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        finally:
            writer.close()


@pytest.fixture
async def panel():
    p = PanelTiruan({50: 1450, 51: 30, 52: 31})
    await p.start()
    yield p
    await p.stop()


class TestPembacaan:
    async def test_membaca_register(self, panel: PanelTiruan) -> None:
        klien = ModbusTcpClient(host="127.0.0.1", port=panel.port)
        assert await klien.read_holding(50, 3) == [1450, 30, 31]
        await klien.close()

    async def test_satu_koneksi_dipakai_ulang(self, panel: PanelTiruan) -> None:
        """Panel menahan sesi TCP yang sudah ditutup di FIN_WAIT_2 — ia tidak
        pernah membalas menutup. Koneksi yang dibuka per pembacaan akan menumpuk
        sampai slot panel habis dan ia berhenti menjawab Modbus sama sekali."""
        klien = ModbusTcpClient(host="127.0.0.1", port=panel.port)
        for _ in range(10):
            await klien.read_holding(50, 3)
        await klien.close()
        assert panel.koneksi == 1

    async def test_rentang_panjang_dipecah_per_123(self, panel: PanelTiruan) -> None:
        klien = ModbusTcpClient(host="127.0.0.1", port=panel.port)
        hasil = await klien.read_range(0, 300)
        await klien.close()
        assert len(hasil) == 300
        assert all(j <= MAX_REGISTERS_PER_REQUEST for _, j in panel.permintaan)


class TestJebakanPanel:
    """Dua perilaku panel yang tidak akan terlihat dari dokumen mana pun."""

    @pytest.mark.parametrize("jumlah", [124, 125, 200])
    async def test_permintaan_berlebih_tidak_pernah_terkirim(
        self, panel: PanelTiruan, jumlah: int
    ) -> None:
        """Yang paling berbahaya: `count>125` menggantungkan stack Modbus panel —
        port 502 tetap menerima koneksi tetapi tidak pernah lagi membalas. Karena
        itu permintaan berlebih ditolak SEBELUM ada frame yang terkirim."""
        klien = ModbusTcpClient(host="127.0.0.1", port=panel.port)
        with pytest.raises(ProtocolError) as exc:
            await klien.read_holding(0, jumlah)
        await klien.close()
        assert exc.value.retryable is False
        assert panel.permintaan == [], "frame tidak boleh sampai ke panel"

    async def test_batas_123_bukan_125(self) -> None:
        """124 membuat 1 register terakhir sampah, 125 membuat 2 register
        terakhir sampah — dan panel tetap membalas byte-count yang tampak benar,
        jadi kerusakannya tidak bisa dideteksi dari bentuk frame."""
        assert MAX_REGISTERS_PER_REQUEST == 123


class TestKetahanan:
    async def test_transaction_id_tidak_cocok_ditolak(self) -> None:
        """Balasan telat dari permintaan sebelumnya dibuang, bukan dipakai.
        Tanpa ini seluruh nilai bisa tergeser satu langkah tanpa ketahuan — dan
        data yang bergeser jauh lebih berbahaya daripada data hilang."""

        async def salah_tid(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            header = await reader.readexactly(7)
            _tid, _p, panjang, _u = struct.unpack(">HHHB", header)
            await reader.readexactly(panjang - 1)
            badan = struct.pack(">BB", 3, 2) + struct.pack(">H", 99)
            writer.write(struct.pack(">HHH", 9999, 0, len(badan) + 1) + b"\x01" + badan)
            await writer.drain()

        server = await asyncio.start_server(salah_tid, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        klien = ModbusTcpClient(host="127.0.0.1", port=port)
        with pytest.raises(ProtocolError, match="transaction id"):
            await klien.read_holding(0, 1)
        await klien.close()
        server.close()
        await server.wait_closed()

    async def test_panel_diam_memberi_petunjuk_menunggu(self) -> None:
        """Petunjuknya penting: menyambung ulang terus-menerus justru menahan
        panel tetap tergantung."""

        async def diam(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await reader.readexactly(7)
            await asyncio.sleep(5)

        server = await asyncio.start_server(diam, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        klien = ModbusTcpClient(host="127.0.0.1", port=port, timeout_seconds=0.3)
        with pytest.raises(ProtocolError) as exc:
            await klien.read_holding(0, 1)
        assert "diam" in str(exc.value.details.get("hint", ""))
        await klien.close()
        server.close()
        await server.wait_closed()

    async def test_panel_tidak_ada_dilaporkan_jelas(self) -> None:
        klien = ModbusTcpClient(host="127.0.0.1", port=1, timeout_seconds=1.0)
        with pytest.raises(ProtocolError) as exc:
            await klien.read_holding(0, 1)
        assert exc.value.code == "modbus.connect_failed"


class TestAdapter:
    async def test_membaca_channel_dari_registry(self, panel: PanelTiruan) -> None:
        from fleetview_edge.protocol import LPA104Adapter

        adapter = LPA104Adapter(
            host="127.0.0.1", port=panel.port, channels=["UW50", "UW51", "UW52"]
        )
        await adapter.connect()
        titik = await adapter.read_points()
        await adapter.close()

        assert {t.channel: t.value for t in titik} == {"UW50": 1450, "UW51": 30, "UW52": 31}

    async def test_alamat_berdekatan_digabung_jadi_satu_permintaan(
        self, panel: PanelTiruan
    ) -> None:
        """Panel diperbarui dari RS-485 dengan Group Interval-nya sendiri;
        permintaan tambahan hanya menambah beban tanpa menghasilkan data baru."""
        from fleetview_edge.protocol import LPA104Adapter

        adapter = LPA104Adapter(
            host="127.0.0.1", port=panel.port, channels=["UW50", "UW51", "UW52"]
        )
        await adapter.connect()
        await adapter.read_points()
        await adapter.close()
        assert len(panel.permintaan) == 1

    async def test_alamat_berjauhan_dipisah(self, panel: PanelTiruan) -> None:
        from fleetview_edge.protocol import LPA104Adapter

        adapter = LPA104Adapter(host="127.0.0.1", port=panel.port, channels=["UW50", "UW900"])
        await adapter.connect()
        await adapter.read_points()
        await adapter.close()
        assert len(panel.permintaan) == 2
