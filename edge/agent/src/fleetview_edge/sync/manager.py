"""Pemilihan transport.

Urutannya LAN > Wi-Fi > seluler > USB, tetapi yang menentukan bukan urutan itu
saja: sebuah link baru dianggap tersedia kalau **central benar-benar bisa
dijangkau** melaluinya. Wi-Fi kapal yang berhasil associate tetapi tidak punya
rute adalah kasus paling umum, dan pemeriksaan tingkat antarmuka akan
melaporkannya sebagai sehat.

Seluler punya anggaran kuota bulanan. Saat habis, ia tidak dimatikan — ia turun
prioritas: hanya data kritis yang lewat. Kapal yang kehabisan kuota tetap harus
bisa mengirim alarm mesin.
"""

from __future__ import annotations

from dataclasses import dataclass

from fleetview_common import get_logger
from fleetview_contracts import SyncPriority
from fleetview_edge.sync.base import TransportAdapter, TransportKind

__all__ = ["NetworkManager", "TransportSlot"]

log = get_logger(__name__)

#: Urutan pilihan. LAN paling murah dan paling cepat; USB terakhir karena
#: butuh manusia yang membawanya.
PREFERENCE: dict[TransportKind, int] = {
    TransportKind.LAN: 0,
    TransportKind.WIFI: 1,
    TransportKind.CELLULAR: 2,
    TransportKind.USB: 3,
}


@dataclass
class TransportSlot:
    """Satu transport beserta batasan operasionalnya."""

    adapter: TransportAdapter
    max_priority: SyncPriority = SyncPriority.RAW
    """Prioritas terendah yang boleh lewat link ini.

    Seluler bermeteran biasanya disetel ke SUMMARY atau CRITICAL: telemetry
    mentah 1 Hz tidak sepadan dengan kuota, alarm mesin sepadan."""

    monthly_budget_bytes: int = 0
    """0 berarti tanpa batas."""
    bytes_used: int = 0

    @property
    def budget_exhausted(self) -> bool:
        return self.monthly_budget_bytes > 0 and self.bytes_used >= self.monthly_budget_bytes

    @property
    def effective_max_priority(self) -> SyncPriority:
        """Kuota habis berarti hanya data kritis yang lewat — bukan mati total."""
        if self.budget_exhausted:
            return SyncPriority.CRITICAL
        return self.max_priority

    def record_usage(self, sent_bytes: int) -> None:
        self.bytes_used += sent_bytes

    def reset_budget(self) -> None:
        self.bytes_used = 0


class NetworkManager:
    """Memilih transport terbaik yang tersedia sekarang."""

    def __init__(self, slots: list[TransportSlot]) -> None:
        self._slots = sorted(slots, key=lambda s: PREFERENCE.get(s.adapter.kind, 99))

    @property
    def slots(self) -> list[TransportSlot]:
        return list(self._slots)

    async def select(self) -> TransportSlot | None:
        """Slot terbaik yang benar-benar bisa dipakai, atau None.

        USB tidak pernah dipilih otomatis: mengekspor ke flashdisk adalah
        tindakan yang diminta operator, bukan yang terjadi diam-diam karena
        jaringan sedang mati.
        """
        for slot in self._slots:
            if slot.adapter.kind is TransportKind.USB:
                continue
            try:
                if await slot.adapter.is_available():
                    return slot
            except Exception as exc:
                log.warning(
                    "sync.transport_probe_failed",
                    transport=slot.adapter.name,
                    error=str(exc),
                )
        return None

    def slot_for(self, kind: TransportKind) -> TransportSlot | None:
        return next((s for s in self._slots if s.adapter.kind is kind), None)

    async def snapshot(self) -> list[dict[str, object]]:
        out: list[dict[str, object]] = []
        for slot in self._slots:
            try:
                health = await slot.adapter.health()
                available, detail = health.available, health.detail
            except Exception as exc:
                available, detail = False, str(exc)
            out.append(
                {
                    "transport": slot.adapter.name,
                    "available": available,
                    "detail": detail,
                    "max_priority": int(slot.effective_max_priority),
                    "budget_exhausted": slot.budget_exhausted,
                    "bytes_used": slot.bytes_used,
                }
            )
        return out
