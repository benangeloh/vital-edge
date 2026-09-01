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

import secrets
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

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

    # -- Setup perangkat ----------------------------------------------------
    #
    # Halaman ini ada supaya teknisi yang memasang Raspberry Pi di kapal tidak
    # perlu menyentuh terminal. Ia satu-satunya halaman yang boleh diakses
    # sebelum perangkat di-provisioning; sisanya dialihkan ke sini, karena
    # menampilkan dasbor kosong tanpa menjelaskan apa yang kurang hanya membuat
    # orang menebak.

    def _configured() -> bool:
        checker = getattr(context, "is_configured", None)
        return bool(checker()) if callable(checker) else True

    @app.middleware("http")
    async def _arahkan_ke_setup(request: Request, call_next: Any) -> Any:
        path = request.url.path
        # `/api/` ikut dikecualikan. Pengalihan 303 ke halaman HTML membuat
        # endpoint kesehatan tidak berguna bagi pemantau — dan pemantau yang
        # menerima HTML akan melaporkan perangkat sehat sebagai perangkat rusak,
        # atau sebaliknya. Permintaan API menjawab JSON apa pun keadaannya.
        exempt = path.startswith(("/setup", "/static", "/api/"))
        if not exempt and not _configured():
            return RedirectResponse("/setup", status_code=303)
        return await call_next(request)

    @app.get("/setup", response_class=HTMLResponse)
    async def setup_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "setup.html",
            {
                **base,
                "active": "/setup",
                "sudah_dikonfigurasi": _configured(),
                "error": request.query_params.get("error"),
                "central_url": request.query_params.get("central_url", ""),
                "client_id": request.query_params.get("client_id", ""),
            },
        )

    @app.post("/setup")
    async def setup_submit(
        request: Request,
        central_url: str = Form(...),
        client_id: str = Form(...),
        secret: str = Form(...),
        pin: str = Form(""),
        ship_name: str = Form(""),
    ) -> RedirectResponse:
        # PIN dicocokkan dengan perbandingan waktu-tetap. Console tidak punya
        # autentikasi lain, dan selama setup ia harus bisa dijangkau dari laptop
        # teknisi di jaringan kapal.
        expected = getattr(context, "setup_pin", None)
        if callable(expected) and not secrets.compare_digest(pin.strip(), str(expected())):
            return _kembali_dengan_error(
                "PIN salah. Jalankan `sudo fleetview-status` di perangkat untuk melihatnya.",
                central_url,
                client_id,
            )

        try:
            nama = await context.provision(
                central_url=central_url.strip(),
                client_id=client_id.strip(),
                secret=secret.strip(),
                ship_name=ship_name.strip() or None,
            )
        except FleetViewError as exc:
            log.warning("console.setup_gagal", code=exc.code, error=exc.message)
            return _kembali_dengan_error(exc.message, central_url, client_id)
        except Exception as exc:
            log.exception("console.setup_gagal")
            return _kembali_dengan_error(str(exc), central_url, client_id)

        log.info("console.setup_berhasil", ship_name=nama)
        return RedirectResponse("/setup?selesai=1", status_code=303)

    def _kembali_dengan_error(pesan: str, central_url: str, client_id: str) -> RedirectResponse:
        # Nilai yang sudah diketik dikembalikan ke formulir. Rahasianya TIDAK —
        # ia akan tampil di bilah alamat dan tercatat di riwayat peramban.
        query = urlencode({"error": pesan, "central_url": central_url, "client_id": client_id})
        return RedirectResponse(f"/setup?{query}", status_code=303)

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
        registry, registry_error = None, None
        getter = getattr(context, "sensor_registry", None)
        if callable(getter):
            try:
                registry = getter()
            except Exception as exc:
                registry_error = str(exc)
        return page(
            request,
            "sensors.html",
            "/sensors",
            sensors=data or [],
            error=error,
            registry=registry,
            registry_error=registry_error,
            edit=request.query_params.get("edit"),
        )

    @app.post("/sensors/simpan")
    async def simpan_sensor(
        sensor_id: str = Form(...),
        channel: str = Form(...),
        metric: str = Form(...),
        unit: str = Form(""),
        field_name: str = Form("value"),
        scale: str = Form("1"),
        offset: str = Form("0"),
        poll_interval_seconds: str = Form("1"),
        min_value: str = Form(""),
        max_value: str = Form(""),
        enabled: str = Form("on"),
    ) -> RedirectResponse:
        """Tambah atau ubah satu sensor.

        Angka diterima sebagai string lalu diurai di sini: input HTML kosong
        mengirimkan "" dan bukan None, dan memaksakan tipe di lapisan FastAPI
        akan menolak formulir dengan pesan yang tidak bisa dibaca teknisi.
        """
        entry: dict[str, Any] = {
            "sensor_id": sensor_id.strip(),
            "channel": channel.strip(),
            "metric": metric.strip(),
            "unit": unit.strip() or None,
            "field_name": field_name.strip() or "value",
            "enabled": enabled == "on",
        }
        for nama, mentah in (
            ("scale", scale),
            ("offset", offset),
            ("poll_interval_seconds", poll_interval_seconds),
        ):
            teks = mentah.strip().replace(",", ".")
            if teks:
                try:
                    entry[nama] = float(teks)
                except ValueError:
                    return _sensor_error(f"{nama} harus berupa angka, dapat {mentah!r}")

        batas: dict[str, float] = {}
        for nama, mentah in (("min_value", min_value), ("max_value", max_value)):
            teks = mentah.strip().replace(",", ".")
            if teks:
                try:
                    batas[nama] = float(teks)
                except ValueError:
                    return _sensor_error(f"{nama} harus berupa angka, dapat {mentah!r}")
        if batas:
            entry["validation"] = batas

        try:
            context.save_sensor(entry)
        except FleetViewError as exc:
            return _sensor_error(exc.message)
        except Exception as exc:
            log.exception("console.sensor_gagal_disimpan")
            return _sensor_error(str(exc))
        return RedirectResponse("/sensors?disimpan=1", status_code=303)

    @app.post("/sensors/hapus")
    async def hapus_sensor(sensor_id: str = Form(...)) -> RedirectResponse:
        try:
            context.remove_sensor(sensor_id.strip())
        except FleetViewError as exc:
            return _sensor_error(exc.message)
        except Exception as exc:
            log.exception("console.sensor_gagal_dihapus")
            return _sensor_error(str(exc))
        return RedirectResponse("/sensors?dihapus=1", status_code=303)

    def _sensor_error(pesan: str) -> RedirectResponse:
        log.warning("console.sensor_ditolak", error=pesan)
        return RedirectResponse(f"/sensors?error={quote(pesan)}", status_code=303)

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
