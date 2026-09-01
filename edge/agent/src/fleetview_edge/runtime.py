"""Runtime Edge Agent — semua komponen dalam satu proses.

Inilah bentuk yang benar-benar berjalan di kapal:

    proses fleetview-edge
    ├── task: collector loop      polling perangkat lapangan
    ├── task: sync engine         batching + upload oportunistik
    └── task: uvicorn (Console)   UI operasional

Satu proses, satu unit systemd, satu aliran log, satu kali restart. Kalau ini
dipecah jadi tiga proses, teknisi di kapal harus tahu proses mana yang mati —
dan dia sedang di tengah laut tanpa siapa pun untuk ditanya.

Urutan penyalaannya penting: **akuisisi dimulai lebih dulu**, sebelum sync dan
sebelum Console. Kalau central tidak terjangkau atau Console gagal mengikat
port, kapal tetap mengumpulkan data. Itu keseluruhan premis offline-first.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal

import uvicorn
from fastapi import FastAPI

from fleetview_common import ConfigError, get_logger
from fleetview_contracts import AcquisitionSource
from fleetview_edge.app import EdgeAgent
from fleetview_edge.collector import BackoffPolicy, Collector, CollectorClock
from fleetview_edge.config import load_sensor_registry
from fleetview_edge.console_context import AgentConsoleContext
from fleetview_edge.outbox import OutboxSink, OutboxStore, SqliteSequenceSource
from fleetview_edge.settings import EdgeSettings
from fleetview_edge.storage import StorageWriter
from fleetview_edge.sync import (
    Batcher,
    FileExportTransport,
    NetworkManager,
    NetworkTransport,
    SyncEngine,
    TransportKind,
    TransportSlot,
)
from fleetview_edge.watchdog import SystemdNotifier

__all__ = ["EdgeRuntime", "run"]

log = get_logger(__name__)


class EdgeRuntime:
    """Merakit dan menjalankan seluruh Edge Agent."""

    def __init__(self, settings: EdgeSettings) -> None:
        self.settings = settings
        self.agent = EdgeAgent(settings)

        self.outbox: OutboxStore | None = None
        self.collector: Collector | None = None
        self.storage: StorageWriter | None = None
        self.sync: SyncEngine | None = None
        self.console: FastAPI | None = None
        self._stopping = asyncio.Event()

    # -- perakitan ----------------------------------------------------------

    def build(self) -> None:
        cfg = self.settings
        if cfg.collector.sensors_path is None:
            raise ConfigError(
                "collector.sensors_path belum disetel; agent tidak bisa tahu "
                "sensor apa yang harus dibaca",
                code="config.sensors_path_missing",
            )

        cfg.storage.data_dir.mkdir(parents=True, exist_ok=True)
        self.outbox = OutboxStore(
            cfg.storage.outbox_path, synchronous_full=cfg.storage.outbox_synchronous_full
        )
        self.storage = self.agent.build_writer()

        registry = load_sensor_registry(cfg.collector.sensors_path)
        source = (
            AcquisitionSource.SIMULATED
            if cfg.collector.adapter in {"mock", "simulator"}
            else AcquisitionSource.LIVE
        )
        self.collector = Collector(
            adapter=self.agent.build_adapter(),
            registry=registry,
            # OutboxSink adalah write barrier: data durable sebelum apa pun
            # terjadi padanya. StorageWriter berada di hilirnya dan boleh gagal.
            sink=OutboxSink(self.outbox, downstream=self.storage),
            ship_id=cfg.ship.ship_id,
            device_id=cfg.ship.device_id,
            clock=CollectorClock(
                SqliteSequenceSource(self.outbox),
                jump_threshold_seconds=cfg.collector.clock_jump_threshold_seconds,
            ),
            poll_interval_seconds=cfg.collector.poll_interval_seconds,
            poll_timeout_seconds=cfg.collector.poll_timeout_seconds,
            backoff=BackoffPolicy(
                initial_seconds=cfg.collector.backoff_initial_seconds,
                max_seconds=cfg.collector.backoff_max_seconds,
            ),
            source=source,
        )

        self.sync = self._build_sync()
        self.console = self._build_console()

    def _build_sync(self) -> SyncEngine:
        cfg = self.settings
        assert self.outbox is not None

        slots: list[TransportSlot] = []
        if cfg.sync.central_url:
            slots.append(
                TransportSlot(
                    adapter=NetworkTransport(
                        base_url=cfg.sync.central_url,
                        token=cfg.sync.device_token,
                        client_id=cfg.sync.device_client_id,
                        secret=cfg.sync.device_secret,
                        kind=TransportKind.LAN,
                    )
                )
            )
        # Transport USB selalu terdaftar meski medianya belum terpasang:
        # NetworkManager tidak pernah memilihnya otomatis, dan Console perlu
        # slot ini ada supaya tombol ekspor bisa bekerja saat teknisi datang.
        slots.append(
            TransportSlot(
                adapter=FileExportTransport(
                    target_dir=cfg.storage.data_dir / "export",
                    ship_slug=cfg.ship.ship_name.replace(" ", "-"),
                ),
                max_priority=cfg.sync.cellular_max_priority,  # type: ignore[arg-type]
            )
        )

        return SyncEngine(
            store=self.outbox,
            batcher=Batcher(
                self.outbox,
                max_records=cfg.sync.batch_max_records,
                max_bytes=cfg.sync.batch_max_bytes,
                agent_version=self.agent.version,
                config_version=cfg.config_version,
            ),
            manager=NetworkManager(slots),
            backoff=BackoffPolicy(
                initial_seconds=cfg.sync.retry_initial_seconds,
                max_seconds=cfg.sync.retry_max_seconds,
            ),
            max_attempts=cfg.sync.max_attempts,
            acked_grace_seconds=cfg.sync.acked_grace_days * 86_400,
            interval_seconds=cfg.sync.interval_seconds,
        )

    def _build_console(self) -> FastAPI | None:
        if not self.settings.console.enabled:
            return None
        from fleetview_console import create_console_app

        context = AgentConsoleContext(
            settings=self.settings,
            agent_version=self.agent.version,
            collector=self.collector,
            storage=self.storage,
            sync_engine=self.sync,
        )
        return create_console_app(
            context=context,
            ship_name=self.settings.ship.ship_name,
            ship_id=str(self.settings.ship.ship_id),
            agent_version=self.agent.version,
            environment=self.settings.environment,
        )

    # -- siklus hidup -------------------------------------------------------

    async def run(self) -> None:
        """Jalankan semua komponen sampai stop() dipanggil."""
        assert self.collector is not None and self.sync is not None

        tasks: list[asyncio.Task[None]] = [
            # Akuisisi lebih dulu. Kalau central tidak terjangkau, kapal tetap
            # mengumpulkan data — itu keseluruhan premis offline-first.
            asyncio.create_task(self.collector.run(), name="collector"),
            asyncio.create_task(self.sync.run(), name="sync"),
        ]
        if self.console is not None:
            tasks.append(asyncio.create_task(self._serve_console(), name="console"))

        notifier = SystemdNotifier()
        if notifier.enabled:
            notifier.ready()
            interval = notifier.interval_seconds
            if interval is not None:
                tasks.append(
                    asyncio.create_task(self._heartbeat(notifier, interval), name="watchdog")
                )

        log.info(
            "edge_runtime.started",
            components=[t.get_name() for t in tasks],
            ship_id=str(self.settings.ship.ship_id),
        )
        try:
            await self._stopping.wait()
        finally:
            notifier.stopping()
            notifier.close()
            self.collector.stop()
            self.sync.stop()
            for task in tasks:
                task.cancel()
            for task in tasks:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            if self.outbox is not None:
                self.outbox.close()
            log.info("edge_runtime.stopped")

    async def _heartbeat(self, notifier: SystemdNotifier, interval: float) -> None:
        """Laporkan hidup ke systemd — tetapi hanya bila akuisisi benar-benar maju.

        Ini inti dari watchdog. Ping tanpa syarat hanya membuktikan event loop
        berputar, dan itu justru bukan kegagalan yang perlu ditangkap: proses
        yang loop akuisisinya tersangkut pada pembacaan serial yang tidak pernah
        kembali akan tetap mem-ping dengan riang sementara kapal berhenti
        mengumpulkan data.

        Jadi yang diperiksa adalah `polls_attempted` benar-benar bertambah. Kalau
        tidak, ping dihentikan, dan systemd yang memutuskan — persis seperti yang
        diinginkan pada perangkat tanpa pengawas.

        Yang dipakai `polls_attempted`, bukan `polls_succeeded`: perangkat yang
        mati atau kabel yang lepas membuat polling gagal terus-menerus, dan itu
        bukan sesuatu yang bisa diperbaiki dengan restart. Merestart agent
        berulang kali dalam keadaan itu hanya membuang siklus akuisisi dan
        menyulitkan diagnosis.
        """
        assert self.collector is not None
        # -1 menjamin satu ping pada iterasi pertama, apa pun keadaannya. Itu
        # kelonggaran start-up yang disengaja: poll pertama bisa lambat saat
        # negosiasi perangkat, dan merestart karenanya adalah restart palsu.
        last_polls = -1
        while not self._stopping.is_set():
            polls = self.collector.stats.polls_attempted
            if polls != last_polls:
                notifier.alive()
                last_polls = polls
            else:
                log.warning(
                    "watchdog.akuisisi_tidak_maju",
                    polls_attempted=polls,
                    note="ping dihentikan; systemd akan merestart bila terus begini",
                )
            if self.outbox is not None:
                notifier.status(f"polls={polls} pending={self.outbox.pending_count()} record")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stopping.wait(), timeout=interval)

    async def _serve_console(self) -> None:
        assert self.console is not None
        config = uvicorn.Config(
            self.console,
            host=self.settings.console.host,
            port=self.settings.console.port,
            log_config=None,
        )
        await uvicorn.Server(config).serve()

    def stop(self) -> None:
        self._stopping.set()


async def run(settings: EdgeSettings) -> int:
    """Titik masuk async. Menangani SIGTERM supaya systemd bisa menghentikan
    agent dengan rapi — outbox ditutup, dan tidak ada transaksi yang menggantung."""
    runtime = EdgeRuntime(settings)
    runtime.agent.setup()
    runtime.build()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, runtime.stop)

    await runtime.run()
    return 0
