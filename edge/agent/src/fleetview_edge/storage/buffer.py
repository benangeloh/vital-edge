"""Buffer percobaan ulang yang berbatas.

Ini jawaban atas "buffering sederhana jika diperlukan". Sengaja **in-memory dan
berbatas**, bukan persisten.

## Apa yang ditangani dan apa yang tidak

Ditangani: InfluxDB mati sesaat — restart, compaction, lonjakan beban. Data
ditahan di memori lalu ditulis ulang begitu database pulih, dengan timestamp
pengukuran aslinya sehingga mendarat di posisi waktu yang benar.

**Tidak ditangani: mati listrik, dan InfluxDB mati berjam-jam.** Isi buffer
hilang saat proses berakhir, dan saat penuh record tertua dibuang.

Itu keterbatasan yang disengaja, bukan kelalaian. Jaminan durabilitas yang
sesungguhnya adalah tugas outbox di Phase 4 — di situ sebuah pembacaan menjadi
tahan mati listrik sebelum apa pun terjadi padanya, dan penulisan ke InfluxDB
turun menjadi operasi hilir yang boleh gagal. Sampai Phase 4 ada, `dropped`
pada health adalah penanda kehilangan data yang nyata, dan harus selalu nol.
"""

from __future__ import annotations

from collections import deque

from fleetview_common import get_logger
from fleetview_contracts import TelemetryRecord

__all__ = ["BoundedRetryBuffer"]

log = get_logger(__name__)


class BoundedRetryBuffer:
    """Antrean FIFO berbatas untuk record yang menunggu ditulis ulang.

    Args:
        max_records: kapasitas. Saat penuh, record **tertua** dibuang.
    """

    def __init__(self, max_records: int = 10_000) -> None:
        if max_records <= 0:
            raise ValueError(f"max_records harus positif, dapat {max_records}")
        self._queue: deque[TelemetryRecord] = deque()
        self._max = max_records
        self._dropped = 0

    def __len__(self) -> int:
        return len(self._queue)

    @property
    def capacity(self) -> int:
        return self._max

    @property
    def dropped(self) -> int:
        return self._dropped

    @property
    def is_full(self) -> bool:
        return len(self._queue) >= self._max

    def extend(self, records: list[TelemetryRecord]) -> int:
        """Tambahkan record. Kembalikan jumlah yang terpaksa dibuang.

        Yang dibuang adalah yang **tertua**. Pilihan ini disengaja: saat
        penyimpanan sedang bermasalah, kondisi terkini kapal lebih berguna bagi
        operator daripada riwayat beberapa jam lalu. Keduanya sama-sama
        kehilangan data — yang ini menyisakan yang lebih berguna.
        """
        dropped_now = 0
        for record in records:
            if len(self._queue) >= self._max:
                self._queue.popleft()
                dropped_now += 1
            self._queue.append(record)

        if dropped_now:
            self._dropped += dropped_now
            log.error(
                "storage.buffer_overflow",
                dropped_now=dropped_now,
                dropped_total=self._dropped,
                capacity=self._max,
                note=(
                    "DATA HILANG. Buffer in-memory penuh karena penyimpanan tidak "
                    "tersedia terlalu lama. Durabilitas sesungguhnya menyusul di Phase 4."
                ),
            )
        return dropped_now

    def take(self, limit: int) -> list[TelemetryRecord]:
        """Ambil sampai `limit` record tertua, dan keluarkan dari buffer."""
        taken: list[TelemetryRecord] = []
        while self._queue and len(taken) < limit:
            taken.append(self._queue.popleft())
        return taken

    def put_back(self, records: list[TelemetryRecord]) -> int:
        """Kembalikan record ke depan antrean setelah penulisan gagal.

        Urutannya dijaga: record ini lebih tua dari apa pun yang sudah ada di
        buffer, jadi ia harus kembali ke depan, bukan ke belakang.
        """
        dropped_now = 0
        for record in reversed(records):
            if len(self._queue) >= self._max:
                self._queue.pop()  # buang yang terbaru untuk memberi ruang yang lebih tua
                dropped_now += 1
            self._queue.appendleft(record)

        if dropped_now:
            self._dropped += dropped_now
            log.error(
                "storage.buffer_overflow_on_retry",
                dropped_now=dropped_now,
                dropped_total=self._dropped,
                capacity=self._max,
            )
        return dropped_now

    def clear(self) -> None:
        self._queue.clear()
