"""Abstraksi penyimpanan telemetry."""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from enum import StrEnum

from fleetview_common import StorageError
from fleetview_contracts import TelemetryRecord

__all__ = [
    "StorageHealth",
    "StorageRejectedError",
    "StorageState",
    "StorageUnavailableError",
    "TelemetryStore",
]


class StorageState(StrEnum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    """Bisa dijangkau, tetapi sebagian penulisan gagal."""
    UNAVAILABLE = "unavailable"


class StorageUnavailableError(StorageError):
    """Database tidak bisa dijangkau atau sedang menolak beban.

    Retryable: mati sesaat, restart, dan compaction adalah kondisi normal.
    """

    code = "storage.unavailable"
    retryable = True


class StorageRejectedError(StorageError):
    """Database menolak data itu sendiri — bentuknya salah, atau auth ditolak.

    **Tidak** retryable. Mengirim ulang payload yang sama akan ditolak lagi
    selamanya, dan buffer akan tersumbat oleh satu batch rusak sementara data
    baru terbuang di belakangnya.
    """

    code = "storage.rejected"
    retryable = False


@dataclass(frozen=True, slots=True)
class StorageHealth:
    """Kondisi penyimpanan, untuk Health Monitor dan Edge Console."""

    state: StorageState
    reachable: bool = False
    last_success_us: int | None = None
    consecutive_failures: int = 0
    buffered_records: int = 0
    dropped_records: int = 0
    """Record yang hilang karena buffer penuh. **Angka ini harus selalu nol.**
    Kalau tidak nol, ada data yang benar-benar hilang."""
    detail: str | None = None
    counters: dict[str, int] = field(default_factory=dict)

    @property
    def healthy(self) -> bool:
        return self.state is StorageState.HEALTHY


class TelemetryStore(abc.ABC):
    """Kontrak penyimpanan telemetry.

    Aturan untuk setiap implementasi:

    1. **`write()` boleh melempar exception.** Pemanggil (StorageWriter) yang
       menangani dan menyangganya. Menelan error di sini akan menyembunyikan
       kehilangan data.
    2. **Bedakan gagal-sementara dari data-ditolak.** `StorageUnavailableError`
       akan diulang; `StorageRejectedError` tidak. Salah memilih berarti buffer
       tersumbat selamanya oleh satu batch rusak, atau data baik dibuang.
    3. **`health()` tidak boleh melempar.** Ia dipanggil justru saat keadaan
       sedang buruk.
    """

    name: str = "unknown"

    @abc.abstractmethod
    async def write(self, records: list[TelemetryRecord]) -> None:
        """Simpan record. Idempoten pada tingkat titik data.

        Identitas titik InfluxDB adalah measurement + tag + timestamp, sehingga
        menulis ulang record yang sama akan menimpa di tempat, bukan
        menggandakan. Itulah yang membuat percobaan ulang aman.

        Raises:
            StorageUnavailableError: gagal sementara, boleh diulang.
            StorageRejectedError: data ditolak, jangan diulang.
        """

    @abc.abstractmethod
    async def health(self) -> StorageHealth: ...

    @abc.abstractmethod
    async def close(self) -> None:
        """Lepaskan sumber daya. Idempoten, tidak melempar."""

    async def ensure_ready(self) -> None:
        """Siapkan penyimpanan (mis. buat bucket, terapkan retensi).

        Default tidak melakukan apa-apa. Kegagalan di sini tidak boleh
        menghentikan agent — kapal tetap harus mengumpulkan data meski
        penyiapan database gagal.
        """
        return None
