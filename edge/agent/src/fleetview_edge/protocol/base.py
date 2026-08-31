"""Abstraksi perangkat lapangan.

Seluruh sistem di hulu — parser, validator, collector — hanya mengenal
`ProtocolAdapter`. Tidak ada satu pun bagian di luar `protocol/` yang tahu bahwa
register address itu ada.

Ini bukan abstraksi yang dibuat berjaga-jaga. Jalur baca dari LP-A104 belum
terkonfirmasi dan ada enam kandidat yang sangat berbeda satu sama lain — Pi jadi
Modbus slave, penyadapan bus RS-485 secara pasif, baca langsung dari PLC, impor
file log, dan seterusnya. Sebagian menghasilkan aliran real-time, satu
menghasilkan impor berkala. Interface ini yang membuat pilihan itu bisa ditunda
tanpa menyandera sisa sistem. Lihat docs/hardware/LP-A104.md.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from enum import StrEnum

from fleetview_common import ProtocolError

__all__ = [
    "AdapterHealth",
    "LinkState",
    "ProtocolAdapter",
    "ProtocolNotConnectedError",
    "ProtocolTimeoutError",
    "RawPoint",
]


class LinkState(StrEnum):
    """Kondisi sambungan ke perangkat lapangan."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DEGRADED = "degraded"
    """Tersambung, tetapi sebagian pembacaan gagal — kabel longgar atau bus berisik."""


class ProtocolTimeoutError(ProtocolError):
    """Perangkat tidak menjawab dalam batas waktu."""

    code = "protocol.timeout"
    retryable = True


class ProtocolNotConnectedError(ProtocolError):
    """Operasi dipanggil sebelum connect() berhasil."""

    code = "protocol.not_connected"
    retryable = True


@dataclass(frozen=True, slots=True)
class RawPoint:
    """Satu nilai mentah, persis seperti yang diberikan perangkat.

    Belum diskalakan, belum dinamai, belum divalidasi. Penerjemahan
    `channel` menjadi sensor yang bermakna dilakukan Parser lewat registry
    sensor — bukan oleh adapter. Dengan begitu adapter tetap bodoh dan bisa
    ditukar, sementara pemetaan sensor jadi urusan konfigurasi.
    """

    channel: str
    """Pengenal asli di perangkat, mis. `UW65540`, `modbus:1:40001`, `mock:rpm`."""

    value: float | int | bool | str
    read_at_us: int
    """Kapan adapter membaca nilai ini, epoch mikrodetik UTC."""

    device_quality: str | None = None
    """Sinyal kualitas dari perangkat itu sendiri, bila ada. Bukan hasil
    penilaian kita — Validator yang melakukan itu."""


@dataclass(frozen=True, slots=True)
class AdapterHealth:
    """Kondisi adapter, untuk Health Monitor dan Edge Console."""

    link: LinkState
    connected_since_us: int | None = None
    last_success_us: int | None = None
    consecutive_failures: int = 0
    detail: str | None = None
    counters: dict[str, int] = field(default_factory=dict)

    @property
    def healthy(self) -> bool:
        return self.link is LinkState.CONNECTED


class ProtocolAdapter(abc.ABC):
    """Kontrak yang harus dipenuhi setiap adapter perangkat lapangan.

    Aturan yang mengikat semua implementasi:

    1. **Jangan pernah mengarang data.** Kalau pembacaan gagal, lempar exception
       atau jangan sertakan channel itu di hasil. Sebuah nilai palsu yang terlihat
       masuk akal adalah kegagalan terburuk untuk sistem monitoring, karena tidak
       menimbulkan gejala.
    2. **Jangan lakukan skala atau penamaan.** Kembalikan nilai apa adanya;
       Parser yang menerjemahkan.
    3. **`read_points()` boleh mengembalikan sebagian.** Kalau 3 dari 40 channel
       gagal, kembalikan 37 dan laporkan lewat `health()` — jangan gagalkan
       semuanya karena satu sensor rusak.
    4. **Semua method harus aman dipanggil ulang.** Supervisor akan memanggil
       `connect()` berkali-kali saat memulihkan sambungan.
    """

    #: Nama pendek untuk log dan diagnosa, mis. `mock`, `simulator`, `lp_a104`.
    name: str = "unknown"

    @abc.abstractmethod
    async def connect(self) -> None:
        """Buka sambungan ke perangkat. Idempoten.

        Raises:
            ProtocolError: sambungan gagal dibuka.
        """

    @abc.abstractmethod
    async def read_points(self) -> list[RawPoint]:
        """Baca semua channel yang tersedia satu putaran.

        Returns:
            Nilai-nilai yang berhasil dibaca. Boleh kurang dari jumlah channel
            yang dikonfigurasi; boleh kosong.

        Raises:
            ProtocolNotConnectedError: belum tersambung.
            ProtocolTimeoutError: perangkat tidak menjawab.
            ProtocolError: kegagalan lain yang bisa diulang.
        """

    @abc.abstractmethod
    async def health(self) -> AdapterHealth:
        """Kondisi terkini. Tidak boleh melempar exception dan tidak boleh
        melakukan I/O yang memblokir — ini dipanggil justru saat keadaan sedang
        buruk, dan harus tetap menjawab."""

    @abc.abstractmethod
    async def close(self) -> None:
        """Tutup sambungan dan lepaskan sumber daya. Idempoten, tidak melempar."""

    async def __aenter__(self) -> ProtocolAdapter:
        await self.connect()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()
