"""Perakitan Edge Agent.

Phase 1 sengaja hanya merakit dan menyalakan: config dimuat, logging dipasang,
Console dinaikkan. Collector, storage, dan Sync Engine menyusul di Phase 2–4 —
masing-masing dipasang sebagai komponen di sini.
"""

from __future__ import annotations

from fastapi import FastAPI

from fleetview_common import ConfigError, configure_logging, get_logger
from fleetview_console import create_console_app
from fleetview_contracts import AcquisitionSource
from fleetview_edge.collector import BackoffPolicy, Collector, CollectorClock
from fleetview_edge.config import SensorRegistry, load_sensor_registry
from fleetview_edge.console_context import AgentConsoleContext
from fleetview_edge.protocol import (
    LPA104Adapter,
    MockLPAAdapter,
    ProtocolAdapter,
    SimulatorAdapter,
)
from fleetview_edge.settings import EdgeSettings
from fleetview_edge.storage import (
    InfluxTelemetryStore,
    RetentionPolicy,
    StorageWriter,
    TelemetryStore,
)
from fleetview_edge.version import AGENT_VERSION

__all__ = ["EdgeAgent"]

log = get_logger(__name__)


class EdgeAgent:
    """Menyatukan komponen-komponen Edge Agent."""

    def __init__(self, settings: EdgeSettings) -> None:
        self.settings = settings
        self.version = AGENT_VERSION

    def setup(self) -> None:
        """Pasang logging dan verifikasi config. Panggil sebelum run()."""
        configure_logging(
            level=self.settings.log_level,
            fmt=self.settings.log_format,
            service="edge-agent",
            version=self.version,
        )
        self.settings.verify_production_ready()

        log.info(
            "edge_agent.configured",
            ship_id=self.settings.ship_id_label,
            ship_name=self.settings.ship_label,
            device_id=str(self.settings.ship.device_id) if self.settings.ship else "-",
            environment=self.settings.environment,
            config_version=self.settings.config_version,
            console_enabled=self.settings.console.enabled,
            adapter=self.settings.collector.adapter,
        )

        # Rincian penyimpanan lokal dicatat terpisah dan selalu, bukan hanya saat
        # gagal. Saat memasang perangkat, pertanyaan pertama teknisi adalah "ini
        # nyimpen ke mana?" — dan menjawabnya lewat berkas config berarti membuka
        # terminal, yang justru ingin dihindari. Baris ini muncul di journald dan
        # di halaman Log Edge Console.
        #
        # Tokennya TIDAK ikut dicatat; yang dicatat hanya apakah ia sudah ada.
        # Log dikirim ke pusat dan dibaca luas.
        storage = self.settings.storage
        log.info(
            "edge_agent.penyimpanan_lokal",
            url=storage.influx_url,
            org=storage.influx_org,
            bucket=storage.influx_bucket,
            retensi_hari=storage.retention_days,
            token_terpasang=bool(storage.influx_token),
            outbox=str(storage.outbox_path),
        )
        if not storage.influx_token:
            log.warning(
                "edge_agent.token_influx_kosong",
                note=(
                    "setiap penulisan lokal akan ditolak 401; sinkronisasi ke pusat "
                    "tetap berjalan karena outbox adalah write barrier-nya"
                ),
            )

    def build_adapter(self) -> ProtocolAdapter:
        """Pilih adapter perangkat lapangan sesuai config.

        `lp_a104` sengaja tetap bisa dipilih meski belum diimplementasikan —
        adapter-nya akan gagal keras dengan pesan yang menjelaskan kenapa, dan
        itu jauh lebih baik daripada diam-diam jatuh ke simulator dan mengirim
        data palsu yang terlihat sehat sempurna di dashboard.
        """
        choice = self.settings.collector.adapter
        if choice == "mock":
            return MockLPAAdapter({})
        if choice == "simulator":
            return SimulatorAdapter()
        # Channel diambil dari registry sensor, bukan ditanam di kode: alamat
        # UW mana yang berisi apa ditentukan ladder panel, dan itu berbeda per
        # kapal. Adapter hanya perlu tahu alamat mana yang harus dibaca.
        cfg = self.settings.collector
        channels: list[str] = []
        if cfg.sensors_path is not None:
            registry = load_sensor_registry(cfg.sensors_path)
            channels = sorted({s.channel for s in registry.enabled})
        return LPA104Adapter(
            host=cfg.lp_a104_host,
            port=cfg.lp_a104_port,
            unit_id=cfg.lp_a104_unit_id,
            channels=channels,
            timeout_seconds=cfg.poll_timeout_seconds,
        )

    def build_collector(self, sink: object) -> Collector:
        """Rakit Collector. Membutuhkan config sensor.

        Raises:
            ConfigError: collector.sensors_path belum disetel atau tidak valid.
        """
        cfg = self.settings.collector
        if cfg.sensors_path is None:
            raise ConfigError(
                "collector.sensors_path belum disetel; Collector tidak bisa dijalankan "
                "tanpa tahu sensor apa yang harus dibaca",
                code="config.sensors_path_missing",
            )

        registry: SensorRegistry = load_sensor_registry(cfg.sensors_path)
        source = (
            AcquisitionSource.SIMULATED
            if cfg.adapter in {"mock", "simulator"}
            else AcquisitionSource.LIVE
        )

        log.info(
            "edge_agent.collector_built",
            adapter=cfg.adapter,
            sensors=len(registry.enabled),
            source=source.value,
        )

        return Collector(
            adapter=self.build_adapter(),
            registry=registry,
            sink=sink,  # type: ignore[arg-type]
            ship_id=self.settings.require_ship().ship_id,
            device_id=self.settings.require_ship().device_id,
            clock=CollectorClock(jump_threshold_seconds=cfg.clock_jump_threshold_seconds),
            poll_interval_seconds=cfg.poll_interval_seconds,
            poll_timeout_seconds=cfg.poll_timeout_seconds,
            backoff=BackoffPolicy(
                initial_seconds=cfg.backoff_initial_seconds,
                max_seconds=cfg.backoff_max_seconds,
            ),
            source=source,
        )

    def build_store(self) -> TelemetryStore:
        """Bangun penyimpanan telemetry lokal."""
        cfg = self.settings.storage
        return InfluxTelemetryStore(
            url=cfg.influx_url,
            org=cfg.influx_org,
            bucket=cfg.influx_bucket,
            token=cfg.influx_token,
            retention=RetentionPolicy(days=cfg.retention_days),
            timeout_seconds=cfg.influx_timeout_seconds,
        )

    def build_writer(self, store: TelemetryStore | None = None) -> StorageWriter:
        """Bangun sink penyimpanan untuk Collector.

        Writer inilah yang menjamin kegagalan penyimpanan tidak pernah
        menghentikan akuisisi — lihat storage/writer.py.
        """
        cfg = self.settings.storage
        return StorageWriter(
            store or self.build_store(),
            buffer_max_records=cfg.buffer_max_records,
            retry_batch_size=cfg.buffer_retry_batch_size,
        )

    def build_console(
        self,
        *,
        collector: object = None,
        storage: object = None,
        sync_engine: object = None,
    ) -> FastAPI:
        """Bangun aplikasi Edge Console.

        Console adalah package terpisah dengan test dan batasnya sendiri, tetapi
        berjalan di dalam proses yang sama dengan agent. Itu keputusan sadar:
        Raspberry Pi tidak perlu proses kedua, port kedua, dan unit systemd kedua
        hanya untuk menyajikan panel status.

        Komponen boleh None — Console dibuka justru saat sesuatu belum atau gagal
        dirakit, dan bagian yang hilang ditampilkan sebagai "tidak diketahui"
        alih-alih menjatuhkan halaman.
        """
        context = AgentConsoleContext(
            settings=self.settings,
            agent_version=self.version,
            collector=collector,  # type: ignore[arg-type]
            storage=storage,  # type: ignore[arg-type]
            sync_engine=sync_engine,  # type: ignore[arg-type]
        )
        return create_console_app(
            context=context,
            ship_name=self.settings.ship_label,
            ship_id=self.settings.ship_id_label,
            agent_version=self.version,
            environment=self.settings.environment,
        )
