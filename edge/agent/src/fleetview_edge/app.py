"""Perakitan Edge Agent.

Phase 1 sengaja hanya merakit dan menyalakan: config dimuat, logging dipasang,
Console dinaikkan. Collector, storage, dan Sync Engine menyusul di Phase 2–4 —
masing-masing dipasang sebagai komponen di sini.
"""

from __future__ import annotations

from fastapi import FastAPI

from fleetview_common import configure_logging, get_logger
from fleetview_console import create_console_app
from fleetview_edge.settings import EdgeSettings
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
            ship_id=str(self.settings.ship.ship_id),
            ship_name=self.settings.ship.ship_name,
            device_id=str(self.settings.ship.device_id),
            environment=self.settings.environment,
            config_version=self.settings.config_version,
            console_enabled=self.settings.console.enabled,
        )

    def build_console(self) -> FastAPI:
        """Bangun aplikasi Edge Console.

        Console adalah package terpisah dengan test dan batasnya sendiri, tetapi
        berjalan di dalam proses yang sama dengan agent. Itu keputusan sadar:
        Raspberry Pi tidak perlu proses kedua, port kedua, dan unit systemd kedua
        hanya untuk menyajikan dua belas panel status.
        """
        return create_console_app(
            ship_name=self.settings.ship.ship_name,
            ship_id=str(self.settings.ship.ship_id),
            agent_version=self.version,
            environment=self.settings.environment,
        )
