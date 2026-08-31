"""Correlation ID yang mengalir lewat contextvars.

Roadmap menuntut satu `batch_id` bisa ditelusuri dari edge, ke central, sampai
ke dashboard. Itu hanya bekerja kalau setiap komponen memakai nama field yang
sama persis — karena itu pengikatannya ditaruh di sini, bukan diserahkan ke
masing-masing modul.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any

import structlog

__all__ = ["bind_context", "clear_context", "current_context", "log_context"]

# Default None, bukan {}: satu dict mutable akan dipakai bersama oleh semua
# context dan bocor lintas request/task. current_context() yang menormalkannya.
_context: ContextVar[dict[str, Any] | None] = ContextVar("fleetview_context", default=None)


def current_context() -> dict[str, Any]:
    return dict(_context.get() or {})


def bind_context(**values: Any) -> Token[dict[str, Any] | None]:
    """Tambahkan nilai ke context saat ini. Kembalikan token untuk pemulihan."""
    merged = {**(_context.get() or {}), **values}
    token = _context.set(merged)
    structlog.contextvars.bind_contextvars(**values)
    return token


def clear_context() -> None:
    _context.set(None)
    structlog.contextvars.clear_contextvars()


@contextmanager
def log_context(**values: Any) -> Iterator[None]:
    """Ikat nilai ke log selama blok berjalan, lalu pulihkan.

    with log_context(batch_id=str(batch.batch_id), ship_id=str(ship_id)):
        await upload(batch)
    """
    token = bind_context(**values)
    try:
        yield
    finally:
        _context.reset(token)
        structlog.contextvars.unbind_contextvars(*values.keys())
