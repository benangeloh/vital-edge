"""Context palsu untuk menguji Console tanpa agent yang berjalan.

Berada di conftest, bukan modul terpisah: berkas test tidak membentuk paket
(pytest memakai importmode=importlib), sehingga impor relatif tidak tersedia —
dan menjadikannya paket akan bentrok dengan direktori `tests/` di akar repo.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from fleetview_common import now_micros
from fleetview_console.context import (
    ExportTarget,
    LogEntry,
    NetworkLink,
    SensorState,
    SyncSnapshot,
    SystemSnapshot,
)

__all__ = ["FakeContext"]


class FakeContext:
    """Menyediakan data yang wajar, dengan kegagalan yang bisa diskenariokan.

    Kegagalan yang bisa diatur itu penting: seluruh alasan Console ada adalah
    untuk dibuka ketika sesuatu rusak, dan itu mustahil diuji dengan andal kalau
    hanya menunggu kebetulan.
    """

    def __init__(
        self,
        *,
        fail: set[str] | None = None,
        empty: bool = False,
        configured: bool = True,
        pin: str = "123456",
    ) -> None:
        self.fail = fail or set()
        self.empty = empty
        self.sync_triggered = 0
        self.exports: list[str] = []
        self._configured = configured
        self._pin = pin
        self.provisioned: list[dict[str, Any]] = []

    # -- provisioning -------------------------------------------------------

    def is_configured(self) -> bool:
        return self._configured

    def setup_pin(self) -> str:
        return self._pin

    async def provision(
        self, *, central_url: str, client_id: str, secret: str, ship_name: str | None = None
    ) -> str:
        self._guard("provision")
        self.provisioned.append(
            {
                "central_url": central_url,
                "client_id": client_id,
                "secret": secret,
                "ship_name": ship_name,
            }
        )
        self._configured = True
        return ship_name or "KM Hasil Setup"

    def _guard(self, name: str) -> None:
        if name in self.fail:
            raise RuntimeError(f"{name} tidak tersedia")

    async def system(self) -> SystemSnapshot:
        self._guard("system")
        return SystemSnapshot(
            ship_id="11111111-1111-1111-1111-111111111111",
            ship_name="KM Sinar Jaya",
            device_id="22222222-2222-2222-2222-222222222222",
            agent_version="0.1.0",
            environment="production",
            config_version="cfg-2026-08-01-3",
            uptime_seconds=93_600,
            collector_healthy=True,
            collector_detail="1.204 polling berhasil, 2 gagal",
            protocol_name="lp_a104",
            protocol_link="connected",
            storage_healthy=True,
            cpu_percent=17.4,
            memory_percent=41.2,
            temperature_celsius=52.8,
            disk_used_percent=63.0,
            disk_free_bytes=42_000_000_000,
        )

    async def sensors(self) -> list[SensorState]:
        self._guard("sensors")
        if self.empty:
            return []
        now = now_micros()
        return [
            SensorState(
                sensor_id="me_port_rpm",
                metric="rpm",
                unit="rpm",
                value=742.5,
                quality="good",
                last_update_us=now - 2_000_000,
                connected=True,
                tags={"engine": "port"},
            ),
            SensorState(
                sensor_id="me_port_exhaust_temp",
                metric="temperature",
                unit="degC",
                value=1_200.0,
                quality="suspect",
                last_update_us=now - 4_000_000,
                connected=True,
                tags={"engine": "port"},
            ),
            SensorState(
                sensor_id="fuel_level_main",
                metric="fuel_level",
                unit="percent",
                value=None,
                quality="stale",
                last_update_us=now - 900_000_000,
                connected=False,
                tags={},
            ),
            SensorState(
                sensor_id="gps_main",
                metric="position",
                unit=None,
                value=None,
                values={"lat": -7.2575, "lon": 112.7521, "sog": 12.4, "cog": 87.0},
                quality="good",
                last_update_us=now - 1_000_000,
                connected=True,
            ),
        ]

    async def sync(self) -> SyncSnapshot:
        self._guard("sync")
        return SyncSnapshot(
            pending_records=18_400,
            pending_batches=37,
            oldest_pending_age_seconds=9_300.0,
            last_success_us=now_micros() - 600_000_000,
            last_error="central tidak terjangkau",
            active_transport="cellular",
            quarantined_batches=2,
            records_synced=902_311,
            bytes_sent=48_100_000,
            transports=[
                {
                    "transport": "lan",
                    "available": False,
                    "detail": "tidak terhubung",
                    "max_priority": 2,
                    "budget_exhausted": False,
                    "bytes_used": 0,
                },
                {
                    "transport": "cellular",
                    "available": True,
                    "detail": None,
                    "max_priority": 1,
                    "budget_exhausted": True,
                    "bytes_used": 524_288_000,
                },
            ],
        )

    async def network(self) -> list[NetworkLink]:
        self._guard("network")
        return [
            NetworkLink(
                name="LAN",
                kind="lan",
                available=False,
                ip_address=None,
                central_reachable=False,
                detail="tidak ada kabel",
            ),
            NetworkLink(
                name="CELLULAR",
                kind="cellular",
                available=True,
                ip_address="10.64.0.12",
                central_reachable=True,
            ),
        ]

    async def export_targets(self) -> list[ExportTarget]:
        self._guard("export_targets")
        if self.empty:
            return []
        return [
            ExportTarget(
                path="/media/usb0", label="usb0", free_bytes=28_000_000_000, writable=True
            ),
            ExportTarget(path="/media/usb1", label="usb1", free_bytes=1_000_000, writable=False),
        ]

    async def logs(self, *, level: str | None = None, limit: int = 200) -> list[LogEntry]:
        self._guard("logs")
        entries = [
            LogEntry(now_micros() - 60_000_000, "info", "collector", "polling berhasil"),
            LogEntry(now_micros() - 30_000_000, "warning", "sync", "batch ditunda"),
            LogEntry(now_micros() - 10_000_000, "error", "storage", "InfluxDB menolak"),
        ]
        if level:
            entries = [e for e in entries if e.level == level]
        return entries[:limit]

    async def config(self) -> dict[str, Any]:
        self._guard("config")
        return {
            "environment": "production",
            "ship": {"ship_name": "KM Sinar Jaya"},
            "storage": {"influx_token": "••••••••", "influx_bucket": "telemetry"},
            "sync": {"device_token": "(belum diisi)", "central_url": "https://x.example"},
        }

    async def trigger_sync(self) -> str:
        self._guard("trigger_sync")
        self.sync_triggered += 1
        return "3 batch tersetor"

    async def start_export(self, target: str) -> str:
        self._guard("start_export")
        self.exports.append(target)
        return f"5 batch diekspor ke {target}"


@pytest.fixture
def make_context() -> Callable[..., FakeContext]:
    """Factory context palsu.

    Fixture, bukan impor langsung: pytest memakai importmode=importlib, sehingga
    modul conftest tidak bisa diimpor lewat namanya — dan menjadikan direktori
    test ini sebuah paket akan bentrok dengan `tests/` di akar repo.
    """

    def _make(**kwargs: Any) -> FakeContext:
        return FakeContext(**kwargs)

    return _make
