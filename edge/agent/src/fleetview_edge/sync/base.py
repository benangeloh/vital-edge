"""Abstraksi transport.

Empat cara data meninggalkan kapal — LAN, Wi-Fi, seluler, dan USB — tetapi
**hanya ada satu Sync Engine**. Perbedaannya berakhir di sini.

Itu keputusan yang disengaja dan berkonsekuensi: logika batching, checksum,
retry, dan penandaan ACK ditulis sekali dan diuji sekali. Kalau setiap transport
punya jalur sync sendiri, jaminan idempotensi harus dibuktikan empat kali — dan
jalur USB, yang paling jarang dipakai, akan jadi yang paling jarang teruji.

USB bukan database. Ia transport, sama seperti yang lain.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from enum import StrEnum

from fleetview_common import FleetViewError
from fleetview_contracts import Ack, BatchEnvelope

__all__ = [
    "TransportAdapter",
    "TransportKind",
    "TransportRejectedError",
    "TransportResult",
    "TransportUnavailableError",
]


class TransportKind(StrEnum):
    LAN = "lan"
    WIFI = "wifi"
    CELLULAR = "cellular"
    USB = "usb"


class TransportUnavailableError(FleetViewError):
    """Transport tidak bisa dipakai sekarang. Boleh dicoba lagi."""

    code = "transport.unavailable"
    retryable = True


class TransportRejectedError(FleetViewError):
    """Batch ditolak permanen — checksum tidak cocok, auth ditolak, bentuk salah.

    Tidak retryable. Mengirim ulang byte yang sama akan ditolak lagi selamanya,
    dan antrean tersumbat oleh satu batch rusak sementara data baru menumpuk
    di belakangnya.
    """

    code = "transport.rejected"
    retryable = False


@dataclass(frozen=True, slots=True)
class TransportResult:
    """Hasil satu percobaan pengiriman."""

    ack: Ack | None
    """ACK dari central. None berarti belum ada konfirmasi.

    **Batch hanya boleh ditandai synchronized kalau ini terisi.** Export USB
    mengembalikan None: berkas sudah tertulis, tetapi belum ada yang
    mengonfirmasi central menerimanya."""

    bytes_sent: int = 0
    resumed_from_chunk: int = 0
    """Berapa potongan yang sudah ada di server saat percobaan ini dimulai.
    Nol berarti mulai dari awal."""
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class TransportHealth:
    kind: TransportKind
    available: bool
    detail: str | None = None
    counters: dict[str, int] = field(default_factory=dict)


class TransportAdapter(abc.ABC):
    """Kontrak untuk semua cara mengirim batch keluar dari kapal.

    Aturan yang mengikat setiap implementasi:

    1. **Jangan pernah menghapus data lokal.** Transport hanya mengirim; yang
       memutuskan sebuah batch selesai adalah ACK, dan hanya Sync Engine yang
       menandainya.
    2. **Bedakan gagal-sementara dari ditolak-permanen.** Sama seperti di
       storage: salah memilih berarti antrean tersumbat, atau data dibuang.
    3. **Kirim ulang harus aman.** Central mengenali `batch_id` yang sudah
       committed dan memutar ulang ACK-nya, jadi percobaan ulang itu gratis.
    """

    kind: TransportKind

    @property
    def name(self) -> str:
        return self.kind.value

    @abc.abstractmethod
    async def is_available(self) -> bool:
        """Apakah transport ini bisa dipakai sekarang? Tidak boleh melempar."""

    @abc.abstractmethod
    async def send(self, envelope: BatchEnvelope, payload: bytes) -> TransportResult:
        """Kirim satu batch.

        Args:
            envelope: metadata batch, termasuk checksum yang harus diverifikasi
                penerima.
            payload: isi batch yang sudah terkompresi.

        Raises:
            TransportUnavailableError: gagal sementara, boleh diulang.
            TransportRejectedError: ditolak permanen, jangan diulang.
        """

    @abc.abstractmethod
    async def health(self) -> TransportHealth: ...

    async def close(self) -> None:
        return None
