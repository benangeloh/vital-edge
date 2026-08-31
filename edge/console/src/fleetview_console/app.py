"""Aplikasi Edge Console.

Console ini alat operasional dan troubleshooting, bukan dashboard analytics.
Ia harus terbuka dengan cepat lewat LAN kapal yang lambat, di laptop tua atau
di HP teknisi, saat sedang ada masalah — jadi tidak ada build step, tidak ada
runtime Node, tidak ada framework frontend. Jinja2 di sisi server ditambah HTMX
untuk pembaruan sebagian.

Phase 1 hanya menyediakan kerangka dan halaman status. Panel yang sesungguhnya
menyusul di Phase 7, saat sudah ada yang bisa dilaporkan.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from fleetview_common import FleetViewError, get_logger

__all__ = ["create_console_app"]

log = get_logger(__name__)

_HERE = Path(__file__).parent
_TEMPLATES = _HERE / "templates"
_STATIC = _HERE / "static"


def create_console_app(
    *,
    ship_name: str,
    ship_id: str,
    agent_version: str,
    environment: str,
) -> FastAPI:
    """Bangun aplikasi Console.

    Dibuat lewat factory, bukan modul global, supaya test bisa membuat beberapa
    instance dengan config berbeda tanpa saling mengganggu.
    """
    app = FastAPI(
        title=f"FleetView Edge Console — {ship_name}",
        version=agent_version,
        docs_url=None,  # Console dipakai lewat browser oleh operator,
        redoc_url=None,  # bukan lewat API explorer.
        openapi_url=None,
    )

    templates = Jinja2Templates(directory=str(_TEMPLATES))
    app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")

    context = {
        "ship_name": ship_name,
        "ship_id": ship_id,
        "agent_version": agent_version,
        "environment": environment,
    }

    @app.exception_handler(FleetViewError)
    async def _handle_fleetview_error(_request: Request, exc: FleetViewError) -> JSONResponse:
        """Petakan error domain ke status HTTP.

        Error retryable jadi 503 supaya klien tahu boleh mencoba lagi; yang tidak
        retryable jadi 400 supaya klien tahu percuma mengulang. Pembedaan yang
        sama dipakai Sync Engine.
        """
        log.warning("console.error", code=exc.code, message=exc.message, **exc.details)
        return JSONResponse(
            status_code=503 if exc.retryable else 400,
            content={"ok": False, "data": None, "error": exc.to_dict(), "meta": {}},
        )

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "index.html", context)

    @app.get("/api/health")
    async def health() -> dict[str, object]:
        """Liveness probe. Sengaja tanpa dependency — endpoint ini harus tetap
        menjawab justru ketika bagian lain sedang rusak."""
        return {
            "ok": True,
            "data": {"status": "alive", **context},
            "error": None,
            "meta": {},
        }

    return app
