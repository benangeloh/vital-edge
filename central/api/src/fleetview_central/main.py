"""Perakitan aplikasi Central Platform.

Ini modular monolith: satu deployable, dengan batas antar-modul ditegakkan di
level kode. Modul-modul (identity, fleet, ingest, telemetry, syncstate, alerts,
audit) mendaftarkan router-nya di sini dan berkomunikasi lewat service interface
publik — tidak pernah dengan meng-import model ORM milik modul lain.

Phase 1 belum punya modul apa pun. Yang ada baru rangkanya: config, logging,
penanganan error, dan endpoint health.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from fleetview_central.http.envelope import success
from fleetview_central.http.errors import register_error_handlers
from fleetview_central.settings import ApiSettings, load_settings
from fleetview_central.version import API_VERSION
from fleetview_common import configure_logging, get_logger

__all__ = ["create_app"]

log = get_logger(__name__)


def create_app(settings: ApiSettings | None = None) -> FastAPI:
    """Bangun aplikasi FastAPI.

    Dibuat lewat factory supaya test bisa membuat instance dengan config berbeda
    tanpa menyentuh state global.
    """
    settings = settings or load_settings()

    configure_logging(
        level=settings.log_level,
        fmt=settings.log_format,
        service="central-api",
        version=API_VERSION,
    )
    settings.verify_production_ready()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        log.info("api.starting", environment=settings.environment, version=API_VERSION)
        # Phase 5: connection pool Postgres dan client InfluxDB dibuka di sini.
        yield
        log.info("api.stopping")

    app = FastAPI(
        title="FleetView Central Platform",
        version=API_VERSION,
        docs_url="/docs" if settings.environment == "development" else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.environment == "development" else None,
        lifespan=lifespan,
    )

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["*"],
        )

    register_error_handlers(app)

    @app.get("/api/health", tags=["ops"])
    async def health() -> dict[str, object]:
        """Liveness probe. Tanpa dependency, supaya tetap menjawab saat Postgres
        atau InfluxDB sedang bermasalah — justru saat itulah probe ini dibaca."""
        return success({"status": "alive", "version": API_VERSION})

    @app.get("/api/ready", tags=["ops"])
    async def ready() -> dict[str, object]:
        """Readiness probe.

        Phase 5 menambahkan pemeriksaan Postgres dan InfluxDB di sini. Untuk
        sekarang selalu ready, karena memang belum ada dependency.
        """
        return success({"status": "ready", "checks": {}})

    # Phase 5+: app.include_router(ingest.router, prefix="/api/v1/ingest")
    #           app.include_router(fleet.router,  prefix="/api/v1/fleet")

    return app
