"""Setup logging bersama.

Dua mode renderer:
  - `console`: berwarna dan mudah dibaca, untuk pengembangan
  - `json`:    satu baris JSON per event, untuk produksi

Produksi memakai JSON karena log dari 70 kapal pada akhirnya harus bisa di-grep
dan diagregasi. Log berwarna yang enak dibaca manusia tidak berguna saat yang
membacanya adalah mesin.

Logging disalurkan lewat `logging` bawaan Python, bukan langsung ke stderr.
Konsekuensinya penting: log dari pustaka pihak ketiga — uvicorn, httpx, dan
nanti SQLAlchemy serta client InfluxDB — ikut melewati pipeline yang sama dan
keluar dalam format yang sama. Kalau tidak, separuh log di kapal berupa JSON
dan separuhnya teks bebas, dan agregasi jadi tidak mungkin.
"""

from __future__ import annotations

import logging
import sys
from typing import Any, Literal

import structlog

__all__ = ["LogFormat", "configure_logging", "get_logger"]

LogFormat = Literal["console", "json"]


def _add_service_context(*, service: str, version: str) -> Any:
    def processor(
        _logger: Any, _name: str, event_dict: structlog.types.EventDict
    ) -> structlog.types.EventDict:
        event_dict.setdefault("service", service)
        event_dict.setdefault("version", version)
        return event_dict

    return processor


def configure_logging(
    *,
    level: str = "INFO",
    fmt: LogFormat = "json",
    service: str,
    version: str,
) -> None:
    """Pasang structlog dan logging bawaan untuk seluruh proses.

    Panggil sekali saat startup, sebelum ada log yang ditulis.

    Args:
        level: level minimum, mis. `DEBUG`, `INFO`, `WARNING`.
        fmt: `console` untuk dev, `json` untuk produksi.
        service: nama layanan, mis. `edge-agent`. Ikut di setiap baris log.
        version: versi layanan. Ikut di setiap baris log.
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    # Processor yang dipakai bersama oleh event structlog maupun record stdlib,
    # sehingga keduanya menghasilkan bentuk yang identik.
    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        _add_service_context(service=service, version=version),
    ]

    structlog.configure(
        processors=[
            *shared,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    renderer: Any
    exc_processor: Any
    if fmt == "console":
        exc_processor = structlog.processors.format_exc_info
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    else:
        exc_processor = structlog.processors.dict_tracebacks
        renderer = structlog.processors.JSONRenderer()

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            exc_processor,
            renderer,
        ],
    )

    # stderr, bukan stdout: stdout dicadangkan untuk keluaran program yang
    # sesungguhnya (mis. tool export), supaya bisa di-pipe tanpa tercampur log.
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(numeric_level)

    # uvicorn memasang handler sendiri; matikan propagasi gandanya.
    for noisy in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(noisy)
        lg.handlers.clear()
        lg.propagate = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Ambil logger. Pakai `__name__` sebagai argumen di modul biasa."""
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
