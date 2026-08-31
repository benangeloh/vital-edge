"""Timestamping dan penomoran sequence.

Ini komponen kecil yang menopang jaminan paling penting di sistem ini.

Jam di kapal melenceng dan kadang melompat mundur — RTC bisa habis baterai, NTP
menyetel ulang begitu 4G tersambung setelah berhari-hari, atau kru mengubahnya
secara manual. Kalau urutan data mengandalkan wall clock, satu lompatan mundur
merusak batas batch, membuat sequence tumpang tindih, dan pada akhirnya membuat
duplikat mustahil dideteksi.

Karena itu: **sequence yang menjadi patokan urutan, jam tidak pernah.** Wall
clock tetap dicatat karena kita memang butuh tahu kapan sesuatu terjadi, tetapi
ia diperlakukan sebagai data yang bisa saja salah — bukan sebagai kebenaran.

Lompatan jam **ditandai, bukan diperbaiki.** Diam-diam mengoreksinya akan
menghapus bukti bahwa jam kapal bermasalah, padahal itu justru yang perlu
diketahui operator.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

from fleetview_common import get_logger, now_micros

__all__ = [
    "CLOCK_ADJUSTED_TAG",
    "CollectorClock",
    "InMemorySequenceSource",
    "SequenceSource",
    "Stamp",
]

log = get_logger(__name__)

CLOCK_ADJUSTED_TAG = "clock_adjusted"
"""Nama tag yang dilekatkan pada pembacaan yang terambil saat jam melompat."""


class SequenceSource(Protocol):
    """Sumber nomor sequence yang monoton dan tanpa celah.

    Phase 2 memakai implementasi in-memory. Phase 3 menggantinya dengan versi
    yang didukung SQLite, sehingga counter-nya bertahan melewati restart dan
    dialokasikan di dalam transaksi yang sama dengan penulisan outbox — itulah
    yang membuat celah menjadi mustahil secara struktural.
    """

    def allocate(self, count: int) -> int:
        """Pesan `count` nomor berurutan. Kembalikan nomor pertama."""
        ...

    @property
    def current(self) -> int:
        """Nomor terakhir yang sudah dialokasikan."""
        ...


class InMemorySequenceSource:
    """Counter sequence in-memory.

    **Hanya untuk Phase 2.** Counter ini mulai dari nol setiap kali proses
    dijalankan ulang, jadi nomornya akan terpakai ulang setelah restart. Itu
    tidak masalah sekarang karena belum ada yang disimpan maupun disinkronkan,
    tetapi harus diganti di Phase 3 sebelum ada data yang benar-benar bertahan.
    """

    def __init__(self, start: int = 0) -> None:
        self._current = start

    def allocate(self, count: int) -> int:
        if count <= 0:
            raise ValueError(f"count harus positif, dapat {count}")
        first = self._current + 1
        self._current += count
        return first

    @property
    def current(self) -> int:
        return self._current


@dataclass(frozen=True, slots=True)
class Stamp:
    """Nomor sequence dan waktu untuk satu pembacaan."""

    sequence: int
    timestamp_us: int
    clock_adjusted: bool = False
    """True bila wall clock melompat sejak stamp sebelumnya. Pembacaannya tetap
    sah dan tetap disimpan — hanya perlu diperlakukan dengan hati-hati."""


class CollectorClock:
    """Memberi nomor sequence dan timestamp, sambil mengawasi jam sistem.

    Deteksi lompatan bekerja dengan membandingkan dua jam: wall clock (yang bisa
    dilompatkan) dan monotonic clock (yang tidak bisa). Kalau keduanya tidak
    bergerak selaras, berarti wall clock-nya yang berubah.

    Args:
        sequence_source: sumber nomor sequence.
        jump_threshold_seconds: selisih antara kedua jam yang dianggap lompatan.
            Default 2 detik memberi ruang untuk penyesuaian NTP kecil dan jeda
            penjadwalan, tanpa melewatkan lompatan yang sesungguhnya.
    """

    def __init__(
        self,
        sequence_source: SequenceSource | None = None,
        *,
        jump_threshold_seconds: float = 2.0,
    ) -> None:
        self._seq = sequence_source or InMemorySequenceSource()
        self._threshold_us = int(jump_threshold_seconds * 1_000_000)
        self._last_wall_us: int | None = None
        self._last_mono_us: int | None = None
        self._jump_count = 0

    @property
    def jump_count(self) -> int:
        """Berapa kali lompatan jam terdeteksi sejak proses dimulai."""
        return self._jump_count

    @property
    def current_sequence(self) -> int:
        return self._seq.current

    def stamp_batch(self, count: int) -> list[Stamp]:
        """Berikan `count` stamp berurutan dengan timestamp yang sama.

        Semua pembacaan dari satu putaran polling berbagi timestamp — mereka
        memang diambil pada momen yang sama secara logis — tetapi masing-masing
        mendapat nomor sequence sendiri, sehingga urutannya tetap terdefinisi
        meski timestamp-nya identik.
        """
        if count <= 0:
            return []

        wall_us = now_micros()
        mono_us = int(time.monotonic() * 1_000_000)
        adjusted = self._detect_jump(wall_us, mono_us)

        self._last_wall_us = wall_us
        self._last_mono_us = mono_us

        first = self._seq.allocate(count)
        return [
            Stamp(sequence=first + i, timestamp_us=wall_us, clock_adjusted=adjusted)
            for i in range(count)
        ]

    def stamp(self) -> Stamp:
        """Satu stamp. Pintasan untuk `stamp_batch(1)`."""
        return self.stamp_batch(1)[0]

    def _detect_jump(self, wall_us: int, mono_us: int) -> bool:
        if self._last_wall_us is None or self._last_mono_us is None:
            return False

        wall_delta = wall_us - self._last_wall_us
        mono_delta = mono_us - self._last_mono_us
        drift = wall_delta - mono_delta

        if abs(drift) < self._threshold_us:
            return False

        self._jump_count += 1
        log.warning(
            "clock.jump_detected",
            drift_seconds=round(drift / 1_000_000, 3),
            direction="backward" if drift < 0 else "forward",
            wall_delta_seconds=round(wall_delta / 1_000_000, 3),
            monotonic_delta_seconds=round(mono_delta / 1_000_000, 3),
            jump_count=self._jump_count,
            note="pembacaan tetap disimpan dan ditandai; timestamp tidak dikoreksi",
        )
        return True
