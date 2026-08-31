"""Loop akuisisi data.

Merangkai adapter, parser, validator, dan clock menjadi satu putaran:

    poll -> parse -> validate -> stamp -> emit

Yang perlu dipahami tentang perilakunya:

- **Satu putaran polling menghasilkan satu timestamp.** Semua pembacaan dalam
  putaran itu diambil pada momen yang sama secara logis. Masing-masing tetap
  mendapat nomor sequence sendiri agar urutannya terdefinisi.
- **Kegagalan sebagian tidak menggagalkan putaran.** Kalau 3 dari 40 channel
  gagal, 37 sisanya tetap dikumpulkan. Satu sensor rusak tidak boleh membutakan
  seluruh kapal.
- **Tidak ada data yang dikarang.** Sensor yang tidak terbaca tidak menghasilkan
  record; ia menghasilkan penanda staleness pada pembacaan berikutnya.
- **Loop tidak pernah berhenti sendiri.** Kegagalan berujung backoff dan
  percobaan ulang, bukan keluar. Kapal berlayar berbulan-bulan tanpa ada yang
  bisa menyalakan ulang prosesnya.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from uuid import UUID

from fleetview_common import ProtocolError, get_logger, log_context
from fleetview_contracts import AcquisitionSource, Quality, TelemetryRecord
from fleetview_edge.collector.clock import CLOCK_ADJUSTED_TAG, CollectorClock
from fleetview_edge.collector.supervisor import BackoffPolicy, ConnectionSupervisor
from fleetview_edge.config.sensors import SensorRegistry
from fleetview_edge.parser.parser import ParsedValue, TelemetryParser
from fleetview_edge.protocol.base import ProtocolAdapter, RawPoint
from fleetview_edge.validator.rules import SensorValidator

__all__ = ["Collector", "CollectorStats", "TelemetrySink"]

log = get_logger(__name__)

TelemetrySink = Callable[[list[TelemetryRecord]], Awaitable[None]]
"""Ke mana telemetry dikirim setelah dikumpulkan.

Phase 3 menyambungkannya ke outbox SQLite. Sampai saat itu, test memakai sink
in-memory. Sengaja berupa callable, bukan interface: collector tidak perlu tahu
apa pun tentang penyimpanan.
"""


@dataclass
class CollectorStats:
    """Penghitung untuk Health Monitor dan Edge Console."""

    polls_attempted: int = 0
    polls_succeeded: int = 0
    polls_failed: int = 0
    records_emitted: int = 0
    timeouts: int = 0
    reconnects: int = 0
    unknown_channels: set[str] = field(default_factory=set)
    quality_counts: dict[str, int] = field(default_factory=dict)

    def record_quality(self, quality: Quality) -> None:
        self.quality_counts[quality.value] = self.quality_counts.get(quality.value, 0) + 1


class Collector:
    """Mengumpulkan telemetry dari perangkat lapangan menurut jadwal.

    Args:
        adapter: sumber data. Bisa mock, simulator, atau adapter sungguhan.
        registry: konfigurasi sensor.
        sink: tujuan telemetry yang terkumpul.
        ship_id, device_id: identitas yang dilekatkan ke setiap record.
        clock: pemberi sequence dan timestamp.
        poll_interval_seconds: jarak antar putaran polling.
        poll_timeout_seconds: batas waktu satu pembacaan sebelum dianggap timeout.
        source: dari mana data ini berasal; ikut ke setiap record.
    """

    def __init__(
        self,
        *,
        adapter: ProtocolAdapter,
        registry: SensorRegistry,
        sink: TelemetrySink,
        ship_id: UUID,
        device_id: UUID,
        clock: CollectorClock | None = None,
        poll_interval_seconds: float = 1.0,
        poll_timeout_seconds: float = 5.0,
        backoff: BackoffPolicy | None = None,
        source: AcquisitionSource = AcquisitionSource.LIVE,
    ) -> None:
        self._adapter = adapter
        self._registry = registry
        self._sink = sink
        self._ship_id = ship_id
        self._device_id = device_id
        self._clock = clock or CollectorClock()
        self._interval = poll_interval_seconds
        self._timeout = poll_timeout_seconds
        self._source = source

        self._parser = TelemetryParser(registry)
        self._validator = SensorValidator()
        self._supervisor = ConnectionSupervisor(
            adapter, backoff=backoff, on_reconnect=self._on_reconnect
        )
        self._stats = CollectorStats()
        self._stopping = asyncio.Event()
        self._next_due_us: dict[str, int] = {}
        """sensor_id -> kapan record berikutnya boleh dipancarkan.

        Adapter membaca semua channel setiap putaran (perangkat lapangan
        umumnya tidak bisa dibaca sebagian), tetapi tidak semua sensor perlu
        disimpan sesering itu. Level bahan bakar pada 1 Hz hanyalah 5x data
        lebih banyak tanpa 5x informasi — dan pada kapal yang offline 30 hari,
        pemborosan itu langsung terasa di disk dan di kuota."""

    @property
    def stats(self) -> CollectorStats:
        return self._stats

    @property
    def clock(self) -> CollectorClock:
        return self._clock

    def _on_reconnect(self) -> None:
        # Setelah putus lama, nilai sebelumnya sudah tidak relevan. Menyimpannya
        # akan membuat pembacaan pertama setelah pulih tampak seperti lonjakan
        # laju perubahan yang luar biasa — padahal yang lama hanyalah jedanya.
        self._validator.reset()
        self._stats.reconnects += 1

    def stop(self) -> None:
        """Minta loop berhenti setelah putaran berjalan selesai."""
        self._stopping.set()

    async def run(self) -> None:
        """Jalankan loop akuisisi sampai stop() dipanggil.

        Penjadwalannya berbasis tenggat, bukan `sleep(interval)`. Kalau memakai
        sleep polos, waktu yang dihabiskan untuk polling ikut tertambah ke
        interval, dan laju sampling perlahan melenceng — pada 1 Hz selama
        berbulan-bulan, penyimpangannya jadi besar.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time()

        log.info(
            "collector.started",
            adapter=self._adapter.name,
            sensors=len(self._registry.enabled),
            interval_seconds=self._interval,
        )

        try:
            while not self._stopping.is_set():
                await self.poll_once()

                deadline += self._interval
                sleep_for = deadline - loop.time()
                if sleep_for < 0:
                    # Putaran memakan waktu lebih lama dari intervalnya. Jangan
                    # menumpuk utang jadwal — setel ulang tenggat dan lanjut.
                    log.warning(
                        "collector.poll_overrun",
                        overrun_seconds=round(-sleep_for, 3),
                        interval_seconds=self._interval,
                    )
                    deadline = loop.time()
                    continue

                # TimeoutError di sini adalah jalur normal: artinya tidak ada
                # permintaan berhenti selama menunggu, jadi lanjut ke putaran berikutnya.
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._stopping.wait(), timeout=sleep_for)
        finally:
            await self._adapter.close()
            log.info(
                "collector.stopped",
                polls=self._stats.polls_attempted,
                records=self._stats.records_emitted,
                reconnects=self._stats.reconnects,
            )

    async def poll_once(self) -> list[TelemetryRecord]:
        """Jalankan satu putaran. Mengembalikan record yang dihasilkan.

        Tidak pernah melempar exception: kegagalan dicatat, dihitung, dan
        ditangani dengan backoff. Loop 24/7 tidak boleh mati karena satu
        pembacaan yang gagal.
        """
        self._stats.polls_attempted += 1

        if not await self._supervisor.ensure_connected():
            self._stats.polls_failed += 1
            return []

        try:
            points = await asyncio.wait_for(self._adapter.read_points(), timeout=self._timeout)
        except TimeoutError:
            self._stats.timeouts += 1
            self._stats.polls_failed += 1
            self._supervisor.mark_disconnected("timeout")
            log.warning(
                "collector.poll_timeout",
                adapter=self._adapter.name,
                timeout_seconds=self._timeout,
            )
            return []
        except ProtocolError as exc:
            self._stats.polls_failed += 1
            self._supervisor.mark_disconnected(exc.code)
            log.warning(
                "collector.poll_failed",
                adapter=self._adapter.name,
                code=exc.code,
                error=exc.message,
                retryable=exc.retryable,
            )
            return []

        records = self._build_records(points)
        self._stats.polls_succeeded += 1

        if records:
            await self._sink(records)
            self._stats.records_emitted += len(records)

        return records

    def _build_records(self, points: list[RawPoint]) -> list[TelemetryRecord]:
        parsed = self._parser.parse(points)
        self._stats.unknown_channels |= parsed.unknown_channels

        if parsed.failed:
            log.warning("collector.parse_failures", failures=parsed.failed)

        grouped = self._group_by_record(parsed.values)
        grouped = self._filter_due(grouped)
        if not grouped:
            return []

        stamps = self._clock.stamp_batch(len(grouped))
        records: list[TelemetryRecord] = []

        for stamp, (key, values) in zip(stamps, grouped.items(), strict=True):
            sensor = values[0].sensor
            quality = Quality.GOOD
            for pv in values:
                outcome = self._validator.assess(pv.sensor, pv.value, pv.read_at_us)
                if outcome.quality is not Quality.GOOD:
                    quality = outcome.quality

            tags = dict(sensor.tags)
            if stamp.clock_adjusted:
                # Ditandai, tidak dikoreksi. Menyembunyikannya akan menghapus
                # bukti bahwa jam kapal bermasalah.
                tags[CLOCK_ADJUSTED_TAG] = "true"

            self._stats.record_quality(quality)
            records.append(
                TelemetryRecord(
                    ship_id=self._ship_id,
                    device_id=self._device_id,
                    sensor_id=key,
                    sequence_number=stamp.sequence,
                    timestamp=stamp.timestamp_us,
                    metric=sensor.metric,
                    values={pv.field_name: pv.value for pv in values},
                    unit=sensor.unit,
                    quality=quality,
                    source=self._source,
                    tags=tags,
                )
            )

        return records

    def _filter_due(self, grouped: dict[str, list[ParsedValue]]) -> dict[str, list[ParsedValue]]:
        """Buang sensor yang interval-nya belum jatuh tempo."""
        due: dict[str, list[ParsedValue]] = {}
        for sensor_id, values in grouped.items():
            sensor = values[0].sensor
            read_at = values[0].read_at_us
            next_due = self._next_due_us.get(sensor_id)
            if next_due is not None and read_at < next_due:
                continue
            due[sensor_id] = values
            self._next_due_us[sensor_id] = read_at + int(sensor.poll_interval_seconds * 1_000_000)
        return due

    @staticmethod
    def _group_by_record(values: list[ParsedValue]) -> dict[str, list[ParsedValue]]:
        """Kelompokkan nilai menjadi record, satu per sensor_id.

        Sensor multi-field seperti GPS mendaftarkan beberapa entri dengan
        sensor_id yang sama dan field_name berbeda, sehingga lat/lon/sog/cog
        berakhir dalam satu record — dan koordinatnya tidak bisa terpisah.
        """
        grouped: dict[str, list[ParsedValue]] = {}
        for pv in values:
            grouped.setdefault(pv.sensor.sensor_id, []).append(pv)
        return grouped

    async def snapshot_health(self) -> dict[str, object]:
        """Ringkasan untuk Health Monitor dan Edge Console."""
        health = await self._adapter.health()
        with log_context(adapter=self._adapter.name):
            return {
                "adapter": self._adapter.name,
                "link": health.link.value,
                "connected": self._supervisor.connected,
                "polls_attempted": self._stats.polls_attempted,
                "polls_succeeded": self._stats.polls_succeeded,
                "polls_failed": self._stats.polls_failed,
                "timeouts": self._stats.timeouts,
                "reconnects": self._stats.reconnects,
                "records_emitted": self._stats.records_emitted,
                "current_sequence": self._clock.current_sequence,
                "clock_jumps": self._clock.jump_count,
                "unknown_channels": sorted(self._stats.unknown_channels),
                "quality_counts": dict(self._stats.quality_counts),
            }
