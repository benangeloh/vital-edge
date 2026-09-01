"""Menyediakan data Console dari komponen Edge Agent yang sedang berjalan.

Ini satu-satunya tempat Console bertemu bagian dalam agent. Console sendiri
hanya tahu tentang snapshot, sehingga bisa diuji tanpa collector, tanpa
penyimpanan, dan tanpa jaringan.
"""

from __future__ import annotations

import contextlib
import secrets
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fleetview_common import ConfigError, now_micros
from fleetview_console import (
    ExportTarget,
    LogEntry,
    NetworkLink,
    SensorState,
    SyncSnapshot,
    SystemMetrics,
    SystemSnapshot,
    redact_config,
)
from fleetview_edge.collector import Collector
from fleetview_edge.config.sensor_editor import (
    delete_sensor,
    ladder_lines,
    read_sensors,
    upsert_sensor,
)
from fleetview_edge.provisioning import provision, setup_pin
from fleetview_edge.settings import EdgeSettings
from fleetview_edge.storage import StorageWriter
from fleetview_edge.sync import SyncEngine, TransportKind

__all__ = ["AgentConsoleContext"]

#: Lokasi mount media lepas-pasang yang lazim.
_MEDIA_ROOTS = (Path("/media"), Path("/mnt"), Path("/Volumes"))


class AgentConsoleContext:
    """Menjembatani komponen agent dengan Console.

    Setiap komponen boleh None: Console dibuka justru saat sesuatu belum atau
    gagal dirakit, dan bagian yang hilang ditampilkan sebagai "tidak diketahui"
    alih-alih menjatuhkan halaman.
    """

    def __init__(
        self,
        *,
        settings: EdgeSettings,
        agent_version: str,
        collector: Collector | None = None,
        storage: StorageWriter | None = None,
        sync_engine: SyncEngine | None = None,
        config_path: Path | None = None,
        on_provisioned: Callable[[], None] | None = None,
    ) -> None:
        self._settings = settings
        self._version = agent_version
        self._collector = collector
        self._storage = storage
        self._sync = sync_engine
        self._metrics = SystemMetrics(data_dir=settings.storage.data_dir)
        self._config_path = config_path
        self._on_provisioned = on_provisioned

    # -- penyimpanan lokal --------------------------------------------------

    async def storage_detail(self) -> dict[str, Any]:
        """Rincian InfluxDB lokal, untuk dipastikan teknisi tanpa membuka terminal.

        Tidak pernah memuat token — hanya apakah ia sudah terpasang. Halaman ini
        dibuka di kapal, sering di layar yang bisa dilihat orang lain.
        """
        cfg = self._settings.storage
        sandi = (
            self._settings.config_path.parent / "influx-admin.password"
            if (self._settings.config_path)
            else None
        )
        detail: dict[str, Any] = {
            "url": cfg.influx_url,
            "org": cfg.influx_org,
            "bucket": cfg.influx_bucket,
            "username": cfg.influx_username,
            "sandi_tersimpan": bool(sandi and sandi.exists()),
            "sandi_path": str(sandi) if sandi else None,
            "retention_days": cfg.retention_days,
            "token_terpasang": bool(cfg.influx_token),
            "terjangkau": None,
            "ditulis": None,
            "ditolak": None,
            "tersangga": None,
        }
        if self._storage is not None:
            with contextlib.suppress(Exception):
                health = await self._storage.health()
                detail["terjangkau"] = health.healthy
                detail["ditulis"] = self._storage.written
                detail["ditolak"] = self._storage.rejected
                detail["tersangga"] = health.buffered_records
        return detail

    def storage_password(self, pin: str) -> str:
        """Kata sandi admin InfluxDB, di balik PIN perangkat.

        Tidak ditampilkan begitu saja karena Edge Console tidak punya
        autentikasi dan di kapal ia terbuka ke jaringan. Kata sandi ini memberi
        kendali penuh atas penyimpanan telemetry kapal — termasuk menghapusnya.
        PIN memastikan yang melihatnya adalah orang yang memang memegang
        perangkat, bukan siapa saja yang bisa menjangkaunya lewat jaringan.
        """
        if not secrets.compare_digest(pin.strip(), self.setup_pin()):
            raise ConfigError(
                "PIN salah. Jalankan `sudo fleetview-status` di perangkat untuk melihatnya.",
                code="storage.pin_invalid",
            )
        path = (
            self._settings.config_path.parent / "influx-admin.password"
            if self._settings.config_path
            else None
        )
        if path is None or not path.exists():
            raise ConfigError(
                "kata sandi tidak tersimpan di perangkat ini — InfluxDB kemungkinan "
                "disiapkan manual. Setel ulang dengan: influx user password --name "
                f"{self._settings.storage.influx_username}",
                code="storage.password_not_stored",
            )
        return path.read_text(encoding="utf-8").strip()

    # -- registry sensor ----------------------------------------------------

    def sensor_registry(self) -> dict[str, Any]:
        """Isi registry apa adanya, untuk disunting di Console.

        Berbeda dari `sensors()`, yang menampilkan pembacaan terakhir. Yang ini
        menampilkan definisinya — termasuk sensor yang belum pernah terbaca
        sekali pun, karena justru itu yang perlu diperbaiki.
        """
        path = self._settings.collector.sensors_path
        entries = read_sensors(path) if path else []
        return {
            "adapter": self._settings.collector.adapter,
            "editable": path is not None,
            "path": str(path) if path else None,
            "sensors": entries,
            "ladder": ladder_lines(entries)
            if self._settings.collector.adapter == "lp_a104"
            else "",
        }

    def save_sensor(self, entry: dict[str, Any]) -> str:
        path = self._settings.collector.sensors_path
        if path is None:
            raise ConfigError(
                "lokasi registry sensor tidak diketahui", code="sensor.no_registry_path"
            )
        return upsert_sensor(path, entry, adapter=self._settings.collector.adapter)

    def remove_sensor(self, sensor_id: str) -> bool:
        path = self._settings.collector.sensors_path
        if path is None:
            raise ConfigError(
                "lokasi registry sensor tidak diketahui", code="sensor.no_registry_path"
            )
        return delete_sensor(path, sensor_id)

    # -- provisioning -------------------------------------------------------

    def is_configured(self) -> bool:
        return self._settings.is_configured

    def setup_pin(self) -> str:
        """PIN yang melindungi halaman setup.

        Disimpan di sebelah outbox, bukan di direktori config: direktori config
        dibaca dan disalin saat diagnosis, dan PIN tidak perlu ikut ke mana-mana.
        """
        return setup_pin(self._settings.storage.data_dir / "setup.pin")

    async def provision(
        self, *, central_url: str, client_id: str, secret: str, ship_name: str | None = None
    ) -> str:
        """Tukar kredensial, tulis config, lalu minta agent dijalankan ulang.

        Agent tidak memuat ulang config sendiri. Ia berhenti dengan rapi, dan
        systemd (`Restart=always`) menyalakannya kembali — kali ini dengan
        identitas yang sudah terisi. Jalur itu dipilih karena tidak menuntut hak
        khusus dari Console dan memakai mekanisme yang memang sudah teruji setiap
        kali perangkat reboot.
        """
        if self._config_path is None:
            raise ConfigError(
                "lokasi berkas config tidak diketahui; agent dijalankan tanpa --config?",
                code="provisioning.no_config_path",
            )
        result = await provision(
            central_url=central_url,
            client_id=client_id,
            secret=secret,
            config_path=self._config_path,
            secrets_path=self._config_path.parent / "secrets.env",
            ship_name_override=ship_name,
        )
        if self._on_provisioned is not None:
            self._on_provisioned()
        return result.handshake.ship_name

    # -- sistem -------------------------------------------------------------

    async def system(self) -> SystemSnapshot:
        collector_health: dict[str, Any] = {}
        if self._collector is not None:
            with contextlib.suppress(Exception):
                collector_health = dict(await self._collector.snapshot_health())

        storage_healthy: bool | None = None
        storage_detail: str | None = None
        if self._storage is not None:
            with contextlib.suppress(Exception):
                health = await self._storage.health()
                storage_healthy = health.healthy
                storage_detail = health.detail or (
                    f"{health.buffered_records} record tersangga"
                    if health.buffered_records
                    else None
                )
                if health.dropped_records:
                    storage_detail = f"{health.dropped_records} record HILANG karena buffer penuh"

        disk_percent, disk_free = self._metrics.disk()
        return SystemSnapshot(
            ship_id=self._settings.ship_id_label,
            ship_name=self._settings.ship_label,
            device_id=str(self._settings.ship.device_id) if self._settings.ship else "-",
            agent_version=self._version,
            environment=self._settings.environment,
            config_version=self._settings.config_version,
            uptime_seconds=self._metrics.uptime_seconds(),
            collector_healthy=(
                bool(collector_health.get("connected")) if collector_health else None
            ),
            collector_detail=(
                f"{collector_health.get('polls_succeeded', 0)} polling berhasil, "
                f"{collector_health.get('polls_failed', 0)} gagal"
                if collector_health
                else None
            ),
            protocol_name=collector_health.get("adapter"),
            protocol_link=collector_health.get("link"),
            protocol_detail=(
                f"{len(collector_health.get('unknown_channels') or [])} channel belum dikenal"
                if collector_health.get("unknown_channels")
                else None
            ),
            storage_healthy=storage_healthy,
            storage_detail=storage_detail,
            cpu_percent=self._metrics.cpu_percent(),
            memory_percent=self._metrics.memory_percent(),
            temperature_celsius=self._metrics.temperature_celsius(),
            disk_used_percent=disk_percent,
            disk_free_bytes=disk_free,
        )

    # -- sensor -------------------------------------------------------------

    async def sensors(self) -> list[SensorState]:
        if self._collector is None:
            return []
        registry = self._collector.registry
        latest = self._collector.latest_readings
        now = now_micros()

        out: list[SensorState] = []
        for sensor_id in sorted(registry.sensor_ids):
            signals = registry.by_id(sensor_id)
            if not signals:
                continue
            record = latest.get(sensor_id)
            first = signals[0]
            age_ok = record is not None and (now - record.timestamp) < 60 * 1_000_000
            out.append(
                SensorState(
                    sensor_id=sensor_id,
                    metric=first.metric,
                    unit=first.unit,
                    value=record.value if record else None,
                    values=dict(record.values) if record else {},
                    quality=record.quality.value if record else "unknown",
                    last_update_us=record.timestamp if record else None,
                    connected=age_ok,
                    tags=dict(first.tags),
                )
            )
        return out

    # -- sinkronisasi -------------------------------------------------------

    async def sync(self) -> SyncSnapshot:
        """Snapshot sinkronisasi.

        Memakai `progress()` dan `stats` yang sudah bertipe, bukan `snapshot()`
        yang mengembalikan dict longgar — nilai yang salah tipe di halaman status
        justru muncul saat keadaan sedang buruk dan paling sulit didiagnosis.
        """
        if self._sync is None:
            return SyncSnapshot(
                pending_records=0,
                pending_batches=0,
                oldest_pending_age_seconds=None,
                last_success_us=None,
                last_error=None,
                active_transport=None,
                quarantined_batches=0,
                records_synced=0,
                bytes_sent=0,
            )

        progress = self._sync.progress()
        stats = self._sync.stats
        transports: list[dict[str, Any]] = []
        with contextlib.suppress(Exception):
            transports = list(await self._sync.manager.snapshot())

        return SyncSnapshot(
            pending_records=progress.pending_records,
            pending_batches=progress.pending_batches,
            oldest_pending_age_seconds=progress.oldest_pending_age_seconds,
            last_success_us=progress.last_success_us,
            last_error=progress.last_error,
            active_transport=progress.active_transport,
            quarantined_batches=progress.quarantined_batches,
            records_synced=stats.records_synced,
            bytes_sent=stats.bytes_sent,
            transports=transports,
        )

    async def trigger_sync(self) -> str:
        if self._sync is None:
            raise RuntimeError("Sync Engine belum dirakit")
        acked = await self._sync.sync_once()
        return f"{acked} batch tersetor"

    # -- jaringan -----------------------------------------------------------

    async def network(self) -> list[NetworkLink]:
        if self._sync is None:
            return []
        links: list[NetworkLink] = []
        for entry in await self._sync.manager.snapshot():
            kind = str(entry["transport"])
            links.append(
                NetworkLink(
                    name=kind.upper(),
                    kind=kind,
                    available=bool(entry["available"]),
                    detail=entry.get("detail"),  # type: ignore[arg-type]
                    # Ketersediaan di sini SUDAH berarti central terjangkau —
                    # NetworkManager memakai probe HTTP sungguhan, bukan status
                    # antarmuka.
                    central_reachable=bool(entry["available"]),
                )
            )
        return links

    # -- ekspor -------------------------------------------------------------

    async def export_targets(self) -> list[ExportTarget]:
        """Cari media lepas-pasang yang terpasang.

        Hanya memeriksa lokasi mount yang lazim. Deteksi hotplug sungguhan
        (udev) menyusul saat perangkat kerasnya diketahui — menebaknya sekarang
        akan menghasilkan daftar yang salah di kapal.
        """
        import shutil

        targets: list[ExportTarget] = []
        for root in _MEDIA_ROOTS:
            if not root.is_dir():
                continue
            with contextlib.suppress(OSError):
                for entry in sorted(root.iterdir()):
                    if not entry.is_dir():
                        continue
                    free: int | None = None
                    writable = False
                    with contextlib.suppress(OSError):
                        free = shutil.disk_usage(entry).free
                        probe = entry / ".fleetview_write_test"
                        probe.write_bytes(b"")
                        probe.unlink()
                        writable = True
                    targets.append(
                        ExportTarget(
                            path=str(entry),
                            label=entry.name,
                            free_bytes=free,
                            writable=writable,
                        )
                    )
        return targets

    async def start_export(self, target: str) -> str:
        if self._sync is None:
            raise RuntimeError("Sync Engine belum dirakit")
        slot = self._sync.manager.slot_for(TransportKind.USB)
        if slot is None:
            raise RuntimeError("Transport ekspor belum dikonfigurasi")
        count = await self._sync.export_to(slot)
        return f"{count} batch diekspor ke {target}"

    # -- log ----------------------------------------------------------------

    async def logs(self, *, level: str | None = None, limit: int = 200) -> list[LogEntry]:
        """Log terbaru.

        Phase 7 belum punya penyimpanan log terstruktur — agent menulis JSON ke
        stderr, yang ditangkap journald. Daftar kosong di sini berarti
        "belum diimplementasikan", bukan "tidak ada masalah", dan Console
        mengatakannya apa adanya di layar.
        """
        _ = level, limit
        return []

    # -- konfigurasi --------------------------------------------------------

    async def config(self) -> dict[str, Any]:
        """Config efektif, sudah teredaksi.

        Diredaksi di sini DAN sekali lagi di Console. Halaman itu satu-satunya
        tempat rahasia bisa sampai ke layar, jadi perlindungannya tidak
        bergantung pada satu pihak saja.
        """
        return dict(redact_config(self._settings.model_dump(mode="json")))
