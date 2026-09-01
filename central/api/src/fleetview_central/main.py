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
from fleetview_central.modules.fleet import router as fleet_router
from fleetview_central.modules.identity import router as identity_router
from fleetview_central.modules.ingest import router as ingest_router
from fleetview_central.modules.ops import router as ops_router
from fleetview_central.modules.telemetry import router as telemetry_router
from fleetview_central.platform.db import Database
from fleetview_central.platform.influx import InfluxGateway
from fleetview_central.platform.security import TokenService
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
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        log.info("api.starting", environment=settings.environment, version=API_VERSION)
        app.state.database = Database(settings.postgres_dsn)
        app.state.influx = InfluxGateway(
            url=settings.influx_url,
            org=settings.influx_org,
            bucket=settings.influx_bucket,
            token=settings.influx_token,
        )
        app.state.tokens = TokenService(
            settings.jwt_secret or "dev-only-secret", ttl_seconds=settings.jwt_ttl_seconds
        )
        try:
            yield
        finally:
            await app.state.influx.close()
            await app.state.database.dispose()
            log.info("api.stopping")

    app = FastAPI(
        title="FleetView Central Platform",
        version=API_VERSION,
        description=(
            "API untuk armada 70+ kapal.\n\n"
            "**Tiga bidang yang dipisahkan sengaja:**\n\n"
            "- **Ingest** (`/api/v1/ingest/*`) — dipanggil Edge Agent dengan token "
            "device yang terikat pada satu `ship_id`. Volume tinggi dan bersifat "
            "meledak-ledak.\n"
            "- **Control** (`/api/v1/{ships,devices,config,alerts}/*`) — dipanggil "
            "dashboard dengan token user + RBAC.\n"
            "- **Query** (`/api/v1/telemetry/*`) — hanya baca.\n\n"
            "Pemisahan ini penting secara operasional: badai reconnect seluruh "
            "armada di bidang ingest tidak boleh membuat dashboard tidak bisa dipakai.\n\n"
            "**Semua response memakai envelope yang sama:** "
            "`{ok, data, error, meta}`. Field `error.retryable` memberi tahu klien "
            "apakah mengulang masuk akal — Sync Engine di edge mematuhinya secara harfiah."
        ),
        openapi_tags=[
            {"name": "auth", "description": "Autentikasi user dan device"},
            {"name": "fleet", "description": "Kapal, perangkat, sensor, konfigurasi"},
            {"name": "ingest", "description": "Penerimaan telemetry per batch"},
            {"name": "sync", "description": "Status sinkronisasi per kapal"},
            {"name": "telemetry", "description": "Pembacaan deret waktu"},
            {"name": "ops", "description": "Alert dan operasional"},
            {"name": "health", "description": "Kesehatan platform"},
            {"name": "audit", "description": "Audit log"},
        ],
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

    # Modul mendaftarkan router-nya sendiri. Prefix ada di masing-masing router
    # supaya batas bidang (ingest / control / query) terbaca dari kodenya.
    app.include_router(identity_router.router)
    app.include_router(fleet_router.router)
    app.include_router(ingest_router.router)
    app.include_router(telemetry_router.router)
    app.include_router(ops_router.router)

    # Config dilekatkan di sini, bukan di dalam lifespan: ia bukan resource yang
    # perlu dibuka dan ditutup, dan sebagian test membangun app tanpa menjalankan
    # lifespan sama sekali.
    app.state.settings = settings

    return app
