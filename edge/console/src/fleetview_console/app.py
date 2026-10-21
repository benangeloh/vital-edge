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

import hashlib
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
    ("/", "Ringkasan"),
    ("/sensors", "Sensor"),
    ("/sync", "Sinkronisasi"),
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


def asset_version(static_dir: Path) -> str:
    """Sidik isi CSS dan JS, dipakai sebagai `?v=` di alamat aset.

    Browser yang menyimpan salinan lama sebelum header `no-cache` ada tidak
    memvalidasi ulang alamat yang sama, tetapi pasti mengambil alamat baru.
    Jadi versi harus berasal dari ISI berkas, bukan nomor versi agent: nomor itu
    tidak berubah saat aset diperbaiki.
    """
    digest = hashlib.sha256()
    for name in ("console.css", "console.js"):
        digest.update((static_dir / name).read_bytes())
    return digest.hexdigest()[:10]


def create_console_app(
    *,
    context: ConsoleContext,
    ship_name: str = "",
    ship_id: str = "",
    agent_version: str = "",
    environment: str = "development",
) -> FastAPI:
    app = FastAPI(
        title=f"FleetView Edge Console · {ship_name}",
        version=agent_version,
        docs_url=None,  # Dipakai lewat browser oleh teknisi,
        redoc_url=None,  # bukan lewat API explorer.
        openapi_url=None,
    )

    templates = Jinja2Templates(directory=str(_TEMPLATES))
    register_filters(templates.env)
    app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")

    @app.middleware("http")
    async def _validasi_ulang_aset(request: Request, call_next: Any) -> Any:
        """CSS dan JS wajib divalidasi ulang lewat ETag di setiap pemuatan.

        Tanpa header ini browser menyimpan aset berdasarkan Last-Modified secara
        heuristik, sehingga setelah agent di-upgrade teknisi bisa mendapat
        template baru dengan JS lama. `no-cache` bukan `no-store`: aset tetap
        disimpan, dan di LAN kapal yang lambat yang bepergian hanya balasan 304.
        """
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    base = {
        "ship_name": ship_name,
        "ship_id": ship_id,
        "agent_version": agent_version,
        "environment": environment,
        "nav": NAV,
    }

    def page(request: Request, template: str, active: str, **extra: Any) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            template,
            {**base, "active": active, "asset_v": asset_version(_STATIC), **extra},
        )

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

    # -- Ringkasan -----------------------------------------------------------

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

    async def _sensor_page(
        request: Request,
        *,
        found: list[dict[str, Any]] | None = None,
        found_error: str | None = None,
        draft: dict[str, str] | None = None,
    ) -> HTMLResponse:
        """Halaman Sensor, dengan atau tanpa hasil pencarian.

        Preset dan pencarian opsional: context yang tidak menyediakannya (mis.
        di pengujian) tetap mendapat formulir lengkap seperti sebelumnya.
        """
        data, error = await _safe(context.sensors())
        registry, registry_error = None, None
        getter = getattr(context, "sensor_registry", None)
        if callable(getter):
            try:
                registry = getter()
            except Exception as exc:
                registry_error = str(exc)

        presets: list[dict[str, Any]] | None = None
        preset_getter = getattr(context, "sensor_presets", None)
        if callable(preset_getter):
            try:
                presets = preset_getter() or None
            except Exception:
                log.exception("console.preset_gagal_dibaca")

        return page(
            request,
            "sensors.html",
            "/sensors",
            sensors=data or [],
            error=error,
            registry=registry,
            registry_error=registry_error,
            edit=request.query_params.get("edit"),
            presets=presets,
            can_discover=callable(getattr(context, "discover_channels", None)),
            found=found,
            found_error=found_error,
            draft=draft or {},
        )

    @app.get("/sensors", response_class=HTMLResponse)
    async def sensors(request: Request) -> HTMLResponse:
        return await _sensor_page(request)

    @app.post("/sensors/cari", response_class=HTMLResponse)
    async def cari_sensor(
        request: Request,
        jenis: str = Form(""),
        sensor_id: str = Form(""),
        alamat: str = Form(""),
    ) -> HTMLResponse:
        """Baca perangkat sebentar dan tampilkan register yang sedang berisi nilai.

        Tombol ini berada di dalam formulir tambah sensor, sehingga isian yang
        sudah diketik ikut terkirim dan dikembalikan apa adanya. Mengetik nama
        lalu menekan cari tidak boleh menghapus apa yang sudah diisi.

        POST, bukan GET: pencarian menyentuh perangkat dan bisa memakan beberapa
        detik, jadi tidak boleh terpicu oleh penyegaran halaman atau pratinjau.
        Kegagalannya menjadi pesan di formulir, bukan halaman rusak.
        """
        draft = {"jenis": jenis.strip(), "sensor_id": sensor_id.strip(), "alamat": alamat.strip()}
        found: list[dict[str, Any]] | None = None
        found_error: str | None = None
        finder = getattr(context, "discover_channels", None)
        if not callable(finder):
            found_error = "Perangkat ini belum mendukung pencarian sensor."
        else:
            try:
                found = await finder()
            except FleetViewError as exc:
                found_error = exc.message
            except Exception as exc:
                log.warning("console.cari_sensor_gagal", error=str(exc))
                found_error = str(exc)

        # Skrip di browser meminta hanya area hasil, supaya tombol Cari tidak
        # memuat ulang seluruh halaman. Tanpa skrip, halaman penuh tetap dikirim.
        if request.headers.get("x-requested-with") == "console":
            return page(
                request,
                "partials/cari_hasil.html",
                "/sensors",
                found=found,
                found_error=found_error,
            )
        return await _sensor_page(request, found=found, found_error=found_error, draft=draft)

    @app.post("/sensors/tambah")
    async def tambah_sensor(
        jenis: str = Form(""),
        sensor_id: str = Form(""),
        alamat: str = Form(""),
        channel: str = Form(""),
    ) -> RedirectResponse:
        """Formulir sederhana: jenis, nama, dan alamat. Sisanya dari preset."""
        adder = getattr(context, "add_sensor_simple", None)
        if not callable(adder):
            return _sensor_error("Perangkat ini belum mendukung formulir sederhana.")
        try:
            adder(
                jenis=jenis.strip(),
                sensor_id=sensor_id.strip(),
                alamat=alamat.strip(),
                channel=channel.strip(),
            )
        except FleetViewError as exc:
            return _sensor_error(exc.message)
        except Exception as exc:
            log.exception("console.sensor_gagal_ditambah")
            return _sensor_error(str(exc))
        return RedirectResponse("/sensors?disimpan=1", status_code=303)

    @app.post("/sensors/simpan")
    async def simpan_sensor(
        sensor_id: str = Form(...),
        channel: str = Form(...),
        metric: str = Form(...),
        unit: str = Form(""),
        field_name: str = Form("value"),
        panel_source: str = Form(""),
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
            "panel_source": panel_source.strip().upper() or None,
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

    # -- Ekspor ---------------------------------------------------------------
    #
    # Tidak punya halaman sendiri — digabung ke Log bersama pratinjau
    # penyimpanan, supaya teknisi tidak perlu berpindah-pindah menu hanya untuk
    # melihat "ke mana data ini pergi" dari beberapa sudut (tersimpan di sini,
    # bisa dibawa keluar dari sini, dan sudah tersalin ke pusat sejauh ini).

    @app.post("/export/start")
    async def start_export(request: Request, target: str = Form(...)) -> RedirectResponse:
        try:
            message = await context.start_export(target)
            log.info("console.export_started", target=target, detail=message)
        except Exception as exc:
            log.warning("console.export_failed", target=target, error=str(exc))
            return RedirectResponse("/logs?error=1", status_code=303)
        return RedirectResponse("/logs?started=1", status_code=303)

    # -- Jaringan -----------------------------------------------------------

    @app.get("/network", response_class=HTMLResponse)
    async def network(request: Request) -> HTMLResponse:
        links, error = await _safe(context.network())
        return page(request, "network.html", "/network", links=links or [], error=error)

    # -- Log ----------------------------------------------------------------

    async def _pratinjau(sensor: str) -> tuple[list[dict[str, Any]] | None, str | None]:
        """Titik data terbaru, opsional untuk satu sensor saja."""
        preview_getter = getattr(context, "storage_preview", None)
        if not callable(preview_getter):
            return None, None
        return await _safe(preview_getter(sensor_id=sensor or None))

    @app.get("/partials/pratinjau", response_class=HTMLResponse)
    async def pratinjau_partial(request: Request, sensor: str = "") -> HTMLResponse:
        """Fragmen untuk penyegaran otomatis pratinjau, supaya nilainya mengikuti panel."""
        sensor = sensor.strip()
        pratinjau, pratinjau_error = await _pratinjau(sensor)
        return page(
            request,
            "partials/pratinjau.html",
            "/logs",
            sensor=sensor,
            pratinjau=pratinjau or [],
            pratinjau_error=pratinjau_error,
        )

    @app.get("/logs", response_class=HTMLResponse)
    async def logs(request: Request, level: str | None = None, sensor: str = "") -> HTMLResponse:
        sensor = sensor.strip()
        entries, error = await _safe(context.logs(level=level, limit=200))

        detail_getter = getattr(context, "storage_detail", None)
        penyimpanan, penyimpanan_error = (
            await _safe(detail_getter()) if callable(detail_getter) else (None, None)
        )

        pratinjau, pratinjau_error = await _pratinjau(sensor)
        daftar_sensor, _ = await _safe(context.sensors())

        targets, targets_error = await _safe(context.export_targets())
        sync, _ = await _safe(context.sync())

        return page(
            request,
            "logs.html",
            "/logs",
            entries=entries or [],
            error=error,
            level=level or "",
            penyimpanan=penyimpanan,
            penyimpanan_error=penyimpanan_error,
            pratinjau=pratinjau or [],
            pratinjau_error=pratinjau_error,
            sensor=sensor,
            sensor_ids=[s.sensor_id for s in daftar_sensor or []],
            targets=targets or [],
            targets_error=targets_error,
            sync=sync,
            sandi=request.query_params.get("sandi"),
            sandi_error=request.query_params.get("sandi_error"),
        )

    @app.post("/logs/sandi")
    async def tampilkan_sandi(pin: str = Form(...)) -> RedirectResponse:
        """Tampilkan kata sandi admin InfluxDB, di balik PIN perangkat.

        Dikembalikan lewat query string, dan itu disengaja terbatas: ia akan
        terlihat di bilah alamat dan riwayat peramban, jadi halaman menyarankan
        menutupnya setelah dicatat. Alternatifnya menyimpan sesi di Console —
        yang berarti menambahkan mekanisme autentikasi utuh untuk satu kolom.
        """
        getter = getattr(context, "storage_password", None)
        if not callable(getter):
            return RedirectResponse("/logs", status_code=303)
        try:
            sandi = getter(pin)
        except FleetViewError as exc:
            return RedirectResponse(f"/logs?sandi_error={quote(exc.message)}", status_code=303)
        except Exception as exc:
            log.exception("console.sandi_gagal")
            return RedirectResponse(f"/logs?sandi_error={quote(str(exc))}", status_code=303)
        return RedirectResponse(f"/logs?sandi={quote(sandi)}", status_code=303)

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
