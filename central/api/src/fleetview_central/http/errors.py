"""Exception handler tingkat aplikasi."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from fleetview_central.http.envelope import error
from fleetview_common import AuthError, ConfigError, FleetViewError, get_logger

__all__ = ["register_error_handlers"]

log = get_logger(__name__)

_STATUS_BY_TYPE: dict[type[FleetViewError], int] = {
    AuthError: 401,
    ConfigError: 500,
}


def _status_for(exc: FleetViewError) -> int:
    """Petakan error domain ke status HTTP.

    Aturannya mengikuti kontrak retry di docs/architecture/04-api-boundaries.md:
    4xx berarti jangan diulang, 5xx dan 429 berarti boleh diulang dengan backoff.
    Edge mematuhi pembedaan ini secara harfiah, jadi salah memetakan di sini
    membuat batch yang rusak permanen diulang selamanya — atau sebaliknya,
    membuat kegagalan sementara dianggap fatal.
    """
    for exc_type, status in _STATUS_BY_TYPE.items():
        if isinstance(exc, exc_type):
            return status
    return 503 if exc.retryable else 400


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(FleetViewError)
    async def _domain_error(_request: Request, exc: FleetViewError) -> JSONResponse:
        status = _status_for(exc)
        # `details` di-nest, bukan di-splat. Splat akan bertabrakan dengan
        # kwarg eksplisit di atas — `details={"status": ...}` dari error ingest
        # membuat handler ini sendiri crash, dan 400 yang bersih berubah jadi
        # 500 beserta stack trace. Ditemukan saat menjalankan server sungguhan.
        log.warning(
            "api.domain_error",
            code=exc.code,
            message=exc.message,
            status=status,
            retryable=exc.retryable,
            details=exc.details,
        )
        return JSONResponse(
            status_code=status,
            content=error(
                code=exc.code,
                message=exc.message,
                details=exc.details,
                retryable=exc.retryable,
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=error(
                code="validation.failed",
                message="Request gagal validasi",
                details={"errors": exc.errors()},
                retryable=False,
            ),
        )

    @app.exception_handler(Exception)
    async def _unhandled(_request: Request, exc: Exception) -> JSONResponse:
        # Log detail lengkapnya, tapi jangan bocorkan ke klien: pesan exception
        # internal sering memuat DSN, path, atau potongan query.
        log.exception("api.unhandled_error", error_type=type(exc).__name__)
        return JSONResponse(
            status_code=500,
            content=error(
                code="internal.unexpected",
                message="Terjadi kesalahan internal.",
                retryable=True,
            ),
        )
