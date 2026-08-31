"""Pemulihan sambungan ke perangkat lapangan.

Kapal adalah lingkungan yang keras untuk elektronik: getaran melonggarkan
konektor, listrik mati-nyala, dan bus jadi berisik saat mesin bekerja berat.
Sambungan yang putus bukan kejadian luar biasa — itu kondisi normal yang harus
ditangani, berkali-kali, selama berbulan-bulan, tanpa ada yang mengawasi.

Karena itu supervisor **tidak pernah menyerah**. Backoff-nya melambat sampai
batas tertentu lalu berhenti melambat, sehingga sambungan yang pulih setelah
enam jam tetap terdeteksi dalam hitungan menit, bukan hari.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass

from fleetview_common import ProtocolError, get_logger
from fleetview_edge.protocol.base import LinkState, ProtocolAdapter

__all__ = ["BackoffPolicy", "ConnectionSupervisor"]

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class BackoffPolicy:
    """Exponential backoff dengan jitter dan batas atas.

    Jitter penting justru bukan untuk satu kapal, melainkan untuk armada: tanpa
    jitter, 70 kapal yang kehilangan sambungan bersamaan (mati listrik di
    pelabuhan, misalnya) akan mencoba menyambung ulang pada detik yang sama
    berulang kali.
    """

    initial_seconds: float = 1.0
    max_seconds: float = 60.0
    multiplier: float = 2.0
    jitter: float = 0.2
    """Fraksi jeda yang diacak, mis. 0,2 berarti ±20%."""

    def delay_for(self, attempt: int) -> float:
        """Jeda sebelum percobaan ke-`attempt` (dihitung mulai 1)."""
        if attempt <= 0:
            return 0.0
        raw = self.initial_seconds * (self.multiplier ** (attempt - 1))
        capped = min(raw, self.max_seconds)
        spread = capped * self.jitter
        return max(0.0, capped + random.uniform(-spread, spread))


class ConnectionSupervisor:
    """Menjaga adapter tetap tersambung, dan memulihkannya saat putus.

    Args:
        adapter: adapter yang disupervisi.
        backoff: kebijakan penundaan antar percobaan.
        on_reconnect: dipanggil setelah sambungan pulih. Collector memakainya
            untuk mereset state validator, supaya jeda panjang tidak salah
            dibaca sebagai lonjakan laju perubahan.
    """

    def __init__(
        self,
        adapter: ProtocolAdapter,
        *,
        backoff: BackoffPolicy | None = None,
        on_reconnect: object = None,
    ) -> None:
        self._adapter = adapter
        self._backoff = backoff or BackoffPolicy()
        self._on_reconnect = on_reconnect
        self._connected = False
        self._attempts = 0
        self._total_reconnects = 0

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def total_reconnects(self) -> int:
        return self._total_reconnects

    def mark_disconnected(self, reason: str) -> None:
        """Catat bahwa sambungan hilang. Dipanggil collector saat pembacaan gagal."""
        if self._connected:
            log.warning(
                "collector.link_lost",
                adapter=self._adapter.name,
                reason=reason,
            )
        self._connected = False

    async def ensure_connected(self) -> bool:
        """Sambungkan bila perlu, dengan satu kali backoff bila gagal.

        Mengembalikan True bila tersambung. Sengaja hanya satu percobaan per
        panggilan supaya loop collector tetap responsif terhadap shutdown —
        loop yang mencoba selamanya di dalam satu panggilan tidak bisa dibatalkan
        dengan rapi.
        """
        if self._connected:
            return True

        self._attempts += 1
        delay = self._backoff.delay_for(self._attempts - 1)
        if delay > 0:
            log.info(
                "collector.reconnect_waiting",
                adapter=self._adapter.name,
                attempt=self._attempts,
                delay_seconds=round(delay, 2),
            )
            await asyncio.sleep(delay)

        try:
            await self._adapter.connect()
        except ProtocolError as exc:
            log.warning(
                "collector.connect_failed",
                adapter=self._adapter.name,
                attempt=self._attempts,
                code=exc.code,
                error=exc.message,
            )
            return False

        was_reconnect = self._total_reconnects > 0 or self._attempts > 1
        self._connected = True
        self._attempts = 0
        self._total_reconnects += 1

        log.info(
            "collector.link_established",
            adapter=self._adapter.name,
            reconnect=was_reconnect,
            total_reconnects=self._total_reconnects,
        )
        if was_reconnect and callable(self._on_reconnect):
            self._on_reconnect()
        return True

    async def link_state(self) -> LinkState:
        health = await self._adapter.health()
        return health.link
