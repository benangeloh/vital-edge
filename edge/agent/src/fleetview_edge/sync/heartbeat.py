"""Heartbeat: kapal melaporkan kondisinya ke central secara berkala.

Dikirim terlepas dari ada tidaknya data untuk disinkronkan. Tanpa ini central
hanya tahu kapal "hidup" dari batch telemetry, dan tidak punya cara melihat disk
yang hampir penuh atau akuisisi yang terputus selama kapal masih diam.

Dua aturan yang membentuk modul ini:

1. **Heartbeat tidak boleh menjatuhkan agent.** Kegagalan apa pun, termasuk
   builder yang melempar, hanya dicatat. Kapal harus tetap mengumpulkan data
   walau pusat tidak terjangkau berminggu-minggu.
2. **Tidak mengarang angka.** Metrik yang tidak terbaca dikirim sebagai `None`
   (atau seluruh blok kesehatan dihilangkan), bukan nol yang terlihat seperti
   perangkat menganggur.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from fleetview_common import get_logger, now_utc
from fleetview_contracts import EdgeHealth, Heartbeat, Transport
from fleetview_edge.sync.manager import NetworkManager
from fleetview_edge.sync.network import NetworkTransport
from fleetview_edge.sync.status import SyncProgress

__all__ = ["HeartbeatBuilder", "HeartbeatSender"]

log = get_logger(__name__)


class HealthMetrics(Protocol):
    def disk(self) -> tuple[float | None, int | None]: ...
    def cpu_percent(self) -> float | None: ...
    def memory_percent(self) -> float | None: ...
    def uptime_seconds(self) -> int | None: ...


class CollectorLike(Protocol):
    @property
    def latest_readings(self) -> Mapping[str, Any]: ...
    async def snapshot_health(self) -> Mapping[str, object]: ...


class StorageLike(Protocol):
    async def health(self) -> Any: ...


class HeartbeatBuilder:
    """Merakit `Heartbeat` dari keadaan agent saat ini."""

    def __init__(
        self,
        *,
        ship_id: UUID,
        device_id: UUID,
        agent_version: str,
        config_version: str,
        metrics: HealthMetrics,
        progress: Callable[[], SyncProgress],
        collector: CollectorLike | None,
        storage: StorageLike | None,
    ) -> None:
        self._ship_id = ship_id
        self._device_id = device_id
        self._agent_version = agent_version
        self._config_version = config_version
        self._metrics = metrics
        self._progress = progress
        self._collector = collector
        self._storage = storage

    async def build(self, *, clock_skew_seconds: float | None) -> Heartbeat:
        progress = self._progress()
        return Heartbeat(
            ship_id=self._ship_id,
            device_id=self._device_id,
            sent_at=now_utc(),
            agent_version=self._agent_version,
            config_version=self._config_version,
            active_transport=_as_transport(progress.active_transport),
            pending_batches=progress.pending_batches,
            pending_records=progress.pending_records,
            oldest_pending_age_seconds=(
                None
                if progress.oldest_pending_age_seconds is None
                else round(progress.oldest_pending_age_seconds)
            ),
            last_telemetry_at=self._last_telemetry_at(),
            health=await self._health(clock_skew_seconds),
        )

    async def _health(self, clock_skew_seconds: float | None) -> EdgeHealth | None:
        disk_percent, _free = self._metrics.disk()
        if disk_percent is None:
            # Lebih baik tidak melapor daripada melapor disk 0%.
            return None

        collector_health = await self._collector_health()
        return EdgeHealth(
            disk_used_percent=disk_percent,
            memory_used_percent=self._metrics.memory_percent(),
            cpu_used_percent=self._metrics.cpu_percent(),
            uptime_seconds=self._metrics.uptime_seconds(),
            collector_healthy=bool(collector_health.get("connected")),
            storage_healthy=await self._storage_healthy(),
            # `degraded` tersambung tetapi sebagian pembacaan gagal: sengaja
            # tidak dihitung sehat, supaya kabel longgar tidak tampak normal.
            protocol_link_healthy=collector_health.get("link") == "connected",
            clock_skew_seconds=clock_skew_seconds,
        )

    async def _collector_health(self) -> Mapping[str, object]:
        if self._collector is None:
            return {}
        try:
            return await self._collector.snapshot_health()
        except Exception as exc:
            log.warning("heartbeat.collector_health_gagal", error=str(exc))
            return {}

    async def _storage_healthy(self) -> bool:
        if self._storage is None:
            return False
        try:
            return bool((await self._storage.health()).healthy)
        except Exception as exc:
            log.warning("heartbeat.storage_health_gagal", error=str(exc))
            return False

    def _last_telemetry_at(self) -> datetime | None:
        if self._collector is None:
            return None
        stamps = [r.timestamp for r in self._collector.latest_readings.values()]
        if not stamps:
            return None
        return datetime.fromtimestamp(max(stamps) / 1_000_000, tz=UTC)


def _as_transport(value: str | None) -> Transport | None:
    if value is None:
        return None
    try:
        return Transport(value)
    except ValueError:
        return None


class HeartbeatSender:
    """Mengirim heartbeat tiap `interval_seconds` lewat transport jaringan terbaik."""

    def __init__(
        self,
        *,
        manager: NetworkManager,
        builder: HeartbeatBuilder,
        interval_seconds: float,
    ) -> None:
        self._manager = manager
        self._builder = builder
        self._interval = interval_seconds
        self._stopping = asyncio.Event()
        self._clock_skew: float | None = None
        self._last_ok: bool | None = None

    @property
    def clock_skew_seconds(self) -> float | None:
        """Selisih jam kapal terhadap server pada heartbeat terakhir yang berhasil.
        Positif berarti jam kapal mendahului."""
        return self._clock_skew

    def stop(self) -> None:
        self._stopping.set()

    async def run(self) -> None:
        """Kirim berkala sampai `stop()`. Tidak pernah berhenti karena kegagalan."""
        while not self._stopping.is_set():
            await self.beat_once()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stopping.wait(), timeout=self._interval)

    async def beat_once(self) -> bool:
        """Satu percobaan. Mengembalikan apakah heartbeat sampai ke central."""
        try:
            slot = await self._manager.select()
            if slot is None or not isinstance(slot.adapter, NetworkTransport):
                return self._record(False, "tidak ada jalur ke central")

            heartbeat = await self._builder.build(clock_skew_seconds=self._clock_skew)
            response = await slot.adapter.send_heartbeat(heartbeat)
        except Exception as exc:
            return self._record(False, str(exc))

        self._clock_skew = (now_utc() - response.server_time).total_seconds()
        return self._record(True, None)

    def _record(self, ok: bool, reason: str | None) -> bool:
        # Dicatat saat keadaan berubah, bukan tiap menit: kapal yang offline
        # berhari-hari tidak boleh memenuhi log dengan baris yang sama.
        if ok != self._last_ok:
            if ok:
                log.info("heartbeat.tersambung")
            else:
                log.warning("heartbeat.gagal", reason=reason)
        self._last_ok = ok
        return ok
