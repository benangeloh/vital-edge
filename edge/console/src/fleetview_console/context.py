"""Kontrak data antara Edge Agent dan Console.

Console tidak pernah menyentuh komponen agent secara langsung. Agent memberikan
sekumpulan callable yang mengembalikan snapshot; Console hanya merendernya.

Pemisahan ini bukan formalitas. Console dibuka teknisi justru **saat sesuatu
sedang rusak** — collector mati, InfluxDB tidak menjawab, jaringan hilang. Kalau
Console mengambil data dengan memanggil komponen secara langsung, kerusakan di
komponen itu ikut menjatuhkan halaman yang seharusnya menjelaskan kerusakannya.
Dengan snapshot, setiap bagian bisa gagal sendiri-sendiri dan sisanya tetap
tampil.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

__all__ = [
    "ConsoleContext",
    "ExportTarget",
    "LogEntry",
    "NetworkLink",
    "SensorState",
    "SyncSnapshot",
    "SystemSnapshot",
]


@dataclass(frozen=True, slots=True)
class SystemSnapshot:
    """Kondisi umum perangkat. Semua metrik boleh None.

    None berarti "tidak bisa dibaca di perangkat ini", bukan nol. Perbedaannya
    penting: suhu 0 °C dan suhu yang tidak terbaca menuntut tindakan berbeda.
    """

    ship_id: str
    ship_name: str
    device_id: str
    agent_version: str
    environment: str
    config_version: str
    uptime_seconds: int | None = None

    collector_healthy: bool | None = None
    collector_detail: str | None = None
    protocol_name: str | None = None
    protocol_link: str | None = None
    protocol_detail: str | None = None
    storage_healthy: bool | None = None
    storage_detail: str | None = None

    cpu_percent: float | None = None
    memory_percent: float | None = None
    temperature_celsius: float | None = None
    disk_used_percent: float | None = None
    disk_free_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class SensorState:
    sensor_id: str
    metric: str
    unit: str | None
    value: float | int | bool | str | None
    """Nilai tunggal untuk sensor skalar. None bila sensornya multi-field."""

    quality: str
    last_update_us: int | None
    connected: bool

    values: dict[str, float | int | bool | str] = field(default_factory=dict)
    """Semua field terukur. Sensor multi-field seperti GPS mengisi ini dengan
    lat/lon/sog/cog — teknisi yang memeriksa GPS butuh melihat koordinatnya,
    bukan tanda strip."""

    tags: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SyncSnapshot:
    pending_records: int
    pending_batches: int
    oldest_pending_age_seconds: float | None
    last_success_us: int | None
    last_error: str | None
    active_transport: str | None
    quarantined_batches: int
    records_synced: int
    bytes_sent: int
    transports: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class NetworkLink:
    name: str
    kind: str
    available: bool
    ip_address: str | None = None
    detail: str | None = None
    central_reachable: bool | None = None


@dataclass(frozen=True, slots=True)
class ExportTarget:
    path: str
    label: str
    free_bytes: int | None
    writable: bool


@dataclass(frozen=True, slots=True)
class LogEntry:
    timestamp_us: int
    level: str
    component: str
    message: str
    detail: str | None = None


class ConsoleContext(Protocol):
    """Yang harus disediakan Edge Agent agar Console bisa merender.

    Setiap method boleh melempar exception — Console menangkapnya per bagian dan
    menampilkan pesan kegagalan di kartu yang bersangkutan saja.
    """

    async def system(self) -> SystemSnapshot: ...
    async def sensors(self) -> list[SensorState]: ...
    async def sync(self) -> SyncSnapshot: ...
    async def network(self) -> list[NetworkLink]: ...
    async def export_targets(self) -> list[ExportTarget]: ...
    async def logs(self, *, level: str | None = None, limit: int = 200) -> list[LogEntry]: ...
    async def config(self) -> dict[str, Any]:
        """Konfigurasi efektif. **Wajib sudah teredaksi** oleh pemanggil."""
        ...

    async def trigger_sync(self) -> str: ...
    async def start_export(self, target: str) -> str: ...

    # -- provisioning -------------------------------------------------------
    #
    # Opsional: Console tetap bisa dipakai dengan context yang tidak
    # menyediakannya (misalnya di pengujian), dan halaman setup akan
    # menyembunyikan dirinya sendiri.

    async def storage_detail(self) -> dict[str, Any]: ...
    def storage_password(self, pin: str) -> str: ...
    def sensor_registry(self) -> dict[str, Any]: ...
    def save_sensor(self, entry: dict[str, Any]) -> str: ...
    def remove_sensor(self, sensor_id: str) -> bool: ...

    def is_configured(self) -> bool: ...
    def setup_pin(self) -> str: ...
    async def provision(
        self, *, central_url: str, client_id: str, secret: str, ship_name: str | None = None
    ) -> str: ...


#: Bentuk callable yang dipakai `create_console_app` bila agent memilih
#: menyediakan fungsi lepas alih-alih objek utuh.
Provider = Callable[[], Awaitable[Any]]
