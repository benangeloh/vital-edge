"""Aplikasi Edge Console.

Alat operasional dan pemeliharaan, bukan dashboard analytics. Ia dibuka teknisi
di atas kapal — sering lewat LAN yang lambat, kadang di tablet dengan sarung
tangan, dan hampir selalu ketika ada yang rusak.

Tiga hal yang menentukan seluruh rancangannya:

**Halaman harus tetap tampil ketika komponennya rusak.** Setiap bagian mengambil
datanya sendiri dan menangkap kegagalannya sendiri. Collector yang mati membuat
satu kartu menampilkan pesan error, bukan seluruh halaman gagal dimuat.

**Harus bekerja tanpa JavaScript.** Semua halaman dirender di server dan lengkap
tanpa JS; skrip hanya menambahkan penyegaran otomatis. Console yang butuh JS
untuk menampilkan status adalah console yang gagal tepat ketika dibutuhkan.

**Tidak ada dependency yang diunduh saat runtime.** Kapal tidak punya internet,
jadi tidak ada CDN. Itu juga alasan HTMX tidak dipakai meski disebut di keputusan
D4: memakainya berarti menyalin blob minified ke repo yang tidak bisa kita
verifikasi isinya. Sekitar 2 KB JavaScript sendiri lebih kecil, dan bisa dibaca.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from fleetview_common import FleetViewError, get_logger
from fleetview_console.context import ConsoleContext
from fleetview_console.filters import register_filters
from fleetview_console.security import redact_config

__all__ = ["create_console_app"]

log = get_logger(__name__)

_HERE = Path(__file__).parent
_TEMPLATES = _HERE / "templates"
_STATIC = _HERE / "static"

#: Urutan menu. Overview lebih dulu karena itu yang dibuka pertama saat
#: teknisi belum tahu apa yang rusak.
NAV = [
    ("/", "Ikhtisar"),
    ("/sensors", "Sensor"),
    ("/sync", "Sinkronisasi"),
    ("/export", "Ekspor Data"),
    ("/network", "Jaringan"),
    ("/logs", "Log"),
    ("/config", "Konfigurasi"),
]


async def _safe(coro: Any) -> tuple[Any, str | None]:
    """Jalankan pengambilan data, kembalikan (hasil, pesan error).

    Inilah yang membuat satu bagian rusak tidak menjatuhkan seluruh halaman.
    """
    try:
        return await coro, None
    except FleetViewError as exc:
        return None, f"{exc.code}: {exc.message}"
    except Exception as exc:
        return None, str(exc)


def create_console_app(
    *,
    context: ConsoleContext,
    ship_name: str = "",
    ship_id: str = "",
    agent_version: str = "",
    environment: str = "development",
) -> FastAPI:
    app = FastAPI(
        title=f"FleetView Edge Console — {ship_name}",
        version=agent_version,
        docs_url=None,  # Dipakai lewat browser oleh teknisi,
        redoc_url=None,  # bukan lewat API explorer.
        openapi_url=None,
    )

    templates = Jinja2Templates(directory=str(_TEMPLATES))
    register_filters(templates.env)
    app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")

    base = {
        "ship_name": ship_name,
        "ship_id": ship_id,
        "agent_version": agent_version,
        "environment": environment,
        "nav": NAV,
    }

    def page(request: Request, template: str, active: str, **extra: Any) -> HTMLResponse:
        return templates.TemplateResponse(request, template, {**base, "active": active, **extra})

    # -- Ikhtisar -----------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    async def overview(request: Request) -> HTMLResponse:
        system, system_error = await _safe(context.system())
        sync, sync_error = await _safe(context.sync())
        return page(
            request,
            "overview.html",
            "/",
            system=system,
            system_error=system_error,
            sync=sync,
            sync_error=sync_error,
        )

    @app.get("/partials/system", response_class=HTMLResponse)
    async def system_partial(request: Request) -> HTMLResponse:
        """Fragmen untuk penyegaran otomatis. Halaman penuh tetap bekerja tanpa ini."""
        system, error = await _safe(context.system())
        return page(request, "partials/system.html", "/", system=system, system_error=error)

    # -- Sensor -------------------------------------------------------------

    @app.get("/sensors", response_class=HTMLResponse)
    async def sensors(request: Request) -> HTMLResponse:
        data, error = await _safe(context.sensors())
        return page(request, "sensors.html", "/sensors", sensors=data or [], error=error)

    @app.get("/partials/sensors", response_class=HTMLResponse)
    async def sensors_partial(request: Request) -> HTMLResponse:
        data, error = await _safe(context.sensors())
        return page(
            request, "partials/sensor_rows.html", "/sensors", sensors=data or [], error=error
        )

    # -- Sinkronisasi -------------------------------------------------------

    @app.get("/sync", response_class=HTMLResponse)
    async def sync_page(request: Request) -> HTMLResponse:
        data, error = await _safe(context.sync())
        return page(request, "sync.html", "/sync", sync=data, error=error)

    @app.post("/sync/trigger")
    async def trigger_sync(request: Request) -> RedirectResponse:
        """Form POST biasa, bukan fetch. Tombol ini harus bekerja meski JS mati."""
        try:
            message = await context.trigger_sync()
            log.info("console.sync_triggered", detail=message)
        except Exception as exc:
            log.warning("console.sync_trigger_failed", error=str(exc))
            return RedirectResponse("/sync?error=1", status_code=303)
        return RedirectResponse("/sync?triggered=1", status_code=303)

    # -- Ekspor -------------------------------------------------------------

    @app.get("/export", response_class=HTMLResponse)
    async def export_page(request: Request) -> HTMLResponse:
        targets, targets_error = await _safe(context.export_targets())
        sync, _ = await _safe(context.sync())
        return page(
            request,
            "export.html",
            "/export",
            targets=targets or [],
            error=targets_error,
            sync=sync,
        )

    @app.post("/export/start")
    async def start_export(request: Request, target: str = Form(...)) -> RedirectResponse:
        try:
            message = await context.start_export(target)
            log.info("console.export_started", target=target, detail=message)
        except Exception as exc:
            log.warning("console.export_failed", target=target, error=str(exc))
            return RedirectResponse("/export?error=1", status_code=303)
        return RedirectResponse("/export?started=1", status_code=303)

    # -- Jaringan -----------------------------------------------------------

    @app.get("/network", response_class=HTMLResponse)
    async def network(request: Request) -> HTMLResponse:
        links, error = await _safe(context.network())
        return page(request, "network.html", "/network", links=links or [], error=error)

    # -- Log ----------------------------------------------------------------

    @app.get("/logs", response_class=HTMLResponse)
    async def logs(request: Request, level: str | None = None) -> HTMLResponse:
        entries, error = await _safe(context.logs(level=level, limit=200))
        return page(
            request,
            "logs.html",
            "/logs",
            entries=entries or [],
            error=error,
            level=level or "",
        )

    # -- Konfigurasi --------------------------------------------------------

    @app.get("/config", response_class=HTMLResponse)
    async def config(request: Request) -> HTMLResponse:
        raw, error = await _safe(context.config())
        # Diredaksi lagi di sini meski context sudah diminta melakukannya.
        # Halaman ini satu-satunya tempat rahasia bisa bocor ke layar, jadi
        # perlindungannya tidak bergantung pada satu pihak saja.
        return page(
            request,
            "config.html",
            "/config",
            config=redact_config(raw or {}),
            error=error,
        )

    # -- API ringan ---------------------------------------------------------

    @app.get("/api/health")
    async def health() -> dict[str, object]:
        """Liveness probe. Tanpa dependency, supaya tetap menjawab justru saat
        komponen lain sedang bermasalah — itulah saat probe ini dibaca."""
        return {
            "ok": True,
            "data": {"status": "alive", "ship_id": ship_id, "agent_version": agent_version},
            "error": None,
            "meta": {},
        }

    @app.get("/api/status")
    async def status_json() -> JSONResponse:
        """Ringkasan mesin-terbaca, untuk skrip pemeliharaan."""
        system, system_error = await _safe(context.system())
        sync, sync_error = await _safe(context.sync())
        return JSONResponse(
            {
                "ok": system_error is None and sync_error is None,
                "data": {
                    "system": system.__dict__ if system else None,
                    "sync": sync.__dict__ if sync else None,
                },
                "error": system_error or sync_error,
                "meta": {},
            }
        )

    @app.exception_handler(FleetViewError)
    async def _handle(_request: Request, exc: FleetViewError) -> JSONResponse:
        log.warning("console.error", code=exc.code, message=exc.message, details=exc.details)
        return JSONResponse(
            status_code=503 if exc.retryable else 400,
            content={"ok": False, "data": None, "error": exc.to_dict(), "meta": {}},
        )

    return app
