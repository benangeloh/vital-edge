"""Klien Modbus TCP untuk LP-A104.

Ditulis dengan asyncio dan pustaka standar, tanpa dependency baru. Modbus TCP
adalah protokol sederhana — header 7 byte lalu PDU — dan menambahkan pustaka
penuh untuk satu function code baca tidak sepadan pada perangkat yang harus
berjalan 24/7 di kapal.

**Batas-batas di sini bukan dari spesifikasi Modbus, melainkan hasil pengukuran
langsung terhadap panel LP-A104 sungguhan.** Panel ini punya dua jebakan yang
tidak akan terlihat dari dokumen mana pun, dan keduanya merusak dengan cara yang
sulit didiagnosis. Rinciannya di masing-masing konstanta di bawah.
"""

from __future__ import annotations

import asyncio
import contextlib
import struct
from typing import Final

from fleetview_common import ProtocolError, get_logger

__all__ = ["ModbusTcpClient"]

log = get_logger(__name__)

FC_READ_HOLDING_REGISTERS: Final = 3

MBAP_HEADER_SIZE: Final = 7
MAX_ADDRESS: Final = 0xFFFF

MAX_REGISTERS_PER_REQUEST: Final = 123
"""Batas aman panel ini, **bukan** batas 125 dari spesifikasi Modbus.

Diukur langsung: `count=124` membuat satu register terakhir berisi sampah, dan
`count=125` membuat dua register terakhir berisi sampah. Yang berbahaya, panel
tetap membalas dengan byte-count yang tampak benar — jadi kerusakannya tidak
bisa dideteksi dari bentuk frame, dan datanya lolos sebagai nilai yang wajar.

Ketahuannya saat menyisir D0..D8191 dengan blok 125: muncul pasangan nilai 3 dan
256 yang berulang persis tiap 125 alamat, tepat di dua register terakhir setiap
blok. Dibaca ulang satu per satu, alamat yang sama ternyata nol.
"""

PANEL_HANG_RECOVERY_SECONDS: Final = 60.0
"""Berapa lama panel harus DIBIARKAN DIAM setelah ia berhenti membalas.

`count > 125` membuat panel menyusun balasan dengan byte-count yang overflow,
dan setelah itu stack Modbus-nya berhenti melayani: port 502 tetap menerima
koneksi TCP, tetapi tidak pernah lagi membalas.

Panel pulih sendiri — tetapi hanya kalau benar-benar dibiarkan tenang. Mencoba
sambung ulang tiap beberapa detik justru menahannya tetap tergantung, karena
slot koneksinya tidak pernah sempat dilepas. Sekali dibiarkan diam, 60 detik
cukup.
"""


def _validate(address: int, count: int) -> None:
    if not 0 <= address <= MAX_ADDRESS:
        raise ProtocolError(
            f"alamat {address} di luar 0..{MAX_ADDRESS}",
            code="modbus.address_out_of_range",
            retryable=False,
        )
    if count < 1 or count > MAX_REGISTERS_PER_REQUEST:
        # Ditolak SEBELUM ada frame yang terkirim. Permintaan berlebih tidak
        # boleh sampai ke panel sama sekali — ia yang menggantungkan panel.
        raise ProtocolError(
            f"count {count} di luar 1..{MAX_REGISTERS_PER_REQUEST}; "
            "melebihi ini menggantungkan panel LP-A104",
            code="modbus.count_out_of_range",
            retryable=False,
        )
    if address + count - 1 > MAX_ADDRESS:
        raise ProtocolError(
            f"rentang {address}..{address + count - 1} melewati batas alamat",
            code="modbus.range_out_of_range",
            retryable=False,
        )


class ModbusTcpClient:
    """Klien Modbus TCP read-only dengan satu koneksi yang dipakai ulang.

    Hanya function code baca yang diimplementasikan, dan itu disengaja: agent
    tidak punya alasan menulis ke panel, dan tidak adanya kode tulis membuat
    kesalahan ke arah itu mustahil terjadi.
    """

    def __init__(
        self,
        *,
        host: str,
        port: int = 502,
        unit_id: int = 1,
        timeout_seconds: float = 5.0,
    ) -> None:
        self._host = host
        self._port = port
        # Panel ini MENGABAIKAN unit id — 0, 1, 2, 3, 9, 11, dan 255 dijawab
        # sama. Tetap dikirim karena frame Modbus menuntutnya, tetapi nilainya
        # tidak menentukan apa pun di perangkat ini.
        self._unit_id = unit_id
        self._timeout = timeout_seconds
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._transaction_id = 0
        self._lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def connect(self) -> None:
        """Buka koneksi. Idempoten.

        Satu koneksi dipakai untuk seluruh siklus polling, bukan satu koneksi
        per pembacaan. Panel menahan sesi TCP yang sudah ditutup di status
        FIN_WAIT_2 — ia tidak pernah membalas menutup — sehingga koneksi yang
        dibuka berulang akan menumpuk sampai slot panel habis dan ia berhenti
        menjawab Modbus sama sekali.
        """
        if self.connected:
            return
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port), timeout=self._timeout
            )
        except (OSError, TimeoutError) as exc:
            raise ProtocolError(
                f"tidak bisa menyambung ke panel {self._host}:{self._port}: {exc}",
                code="modbus.connect_failed",
                details={"host": self._host, "port": self._port},
            ) from exc
        log.info("modbus.terhubung", host=self._host, port=self._port)

    async def close(self) -> None:
        writer, self._writer, self._reader = self._writer, None, None
        if writer is None:
            return
        writer.close()
        # Panel sering tidak membalas menutup — itulah sebabnya sesinya menumpuk
        # di FIN_WAIT_2. Menunggunya bukan alasan melempar.
        with contextlib.suppress(OSError, TimeoutError):
            await asyncio.wait_for(writer.wait_closed(), timeout=2.0)

    async def read_holding(self, address: int, count: int) -> list[int]:
        """Baca holding register (FC03).

        FC03 dan FC04 mengembalikan isi yang sama pada panel ini; FC03 dipilih
        karena itu yang lazim dan sudah terbukti terpakai.
        """
        _validate(address, count)
        async with self._lock:
            return await self._transact(address, count)

    async def read_range(self, address: int, count: int) -> list[int]:
        """Baca rentang sepanjang apa pun, dipecah otomatis per 123.

        Pemecahan dilakukan di sisi kita supaya panel tidak pernah menerima
        permintaan yang melampaui batas amannya.
        """
        if count < 1:
            return []
        hasil: list[int] = []
        sisa, mulai = count, address
        while sisa > 0:
            blok = min(sisa, MAX_REGISTERS_PER_REQUEST)
            hasil.extend(await self.read_holding(mulai, blok))
            mulai += blok
            sisa -= blok
        return hasil

    async def _transact(self, address: int, count: int) -> list[int]:
        await self.connect()
        assert self._reader is not None and self._writer is not None

        self._transaction_id = (self._transaction_id + 1) % 0x10000
        tid = self._transaction_id
        pdu = struct.pack(">BHH", FC_READ_HOLDING_REGISTERS, address, count)
        frame = struct.pack(">HHHB", tid, 0, len(pdu) + 1, self._unit_id) + pdu

        try:
            self._writer.write(frame)
            await self._writer.drain()
            header = await asyncio.wait_for(
                self._reader.readexactly(MBAP_HEADER_SIZE), timeout=self._timeout
            )
            balas_tid, _proto, panjang, _unit = struct.unpack(">HHHB", header)
            badan = await asyncio.wait_for(
                self._reader.readexactly(panjang - 1), timeout=self._timeout
            )
        except (TimeoutError, asyncio.IncompleteReadError, OSError) as exc:
            await self.close()
            raise ProtocolError(
                f"panel tidak membalas: {exc}",
                code="modbus.no_reply",
                details={
                    "hint": (
                        "kalau berulang, HENTIKAN polling dan biarkan panel diam "
                        f"{PANEL_HANG_RECOVERY_SECONDS:.0f} detik; menyambung ulang "
                        "terus-menerus justru menahannya tergantung"
                    )
                },
            ) from exc

        if balas_tid != tid:
            # Balasan telat dari permintaan sebelumnya. Dibuang, bukan dipakai —
            # tanpa ini seluruh nilai bisa tergeser satu langkah tanpa ketahuan,
            # dan data yang bergeser jauh lebih berbahaya daripada data hilang.
            await self.close()
            raise ProtocolError(
                f"transaction id tidak cocok: minta {tid}, dapat {balas_tid}",
                code="modbus.transaction_mismatch",
            )

        fungsi = badan[0]
        if fungsi & 0x80:
            kode = badan[1] if len(badan) > 1 else 0
            raise ProtocolError(
                f"panel menolak permintaan (exception {kode})",
                code="modbus.exception",
                details={"exception_code": kode},
                retryable=False,
            )

        jumlah_byte = badan[1]
        data = badan[2 : 2 + jumlah_byte]
        if jumlah_byte != count * 2 or len(data) != jumlah_byte:
            raise ProtocolError(
                f"balasan tidak utuh: diminta {count} register, "
                f"byte-count {jumlah_byte}, terbaca {len(data)}",
                code="modbus.short_reply",
            )
        return list(struct.unpack(f">{count}H", data))
