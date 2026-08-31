"""Fondasi bersama untuk Edge Agent dan Central Platform.

Berisi hal-hal yang **harus** identik di kedua sisi: format logging, taksonomi
error, penanganan waktu, dan pengikatan correlation ID. Menduplikasinya akan
membuat penelusuran satu batch dari edge sampai dashboard rusak diam-diam saat
keduanya menyimpang.

Package ini tidak berisi logic bisnis dan tidak bergantung pada fleetview_contracts.
"""

from fleetview_common.context import bind_context, clear_context, current_context, log_context
from fleetview_common.errors import (
    AuthError,
    ConfigError,
    FleetViewError,
    ProtocolError,
    StorageError,
    TransportError,
    ValidationError,
)
from fleetview_common.ids import new_id, uuid7
from fleetview_common.logging import LogFormat, configure_logging, get_logger
from fleetview_common.timeutil import from_micros, now_micros, now_utc, to_micros

__all__ = [
    "AuthError",
    "ConfigError",
    "FleetViewError",
    "LogFormat",
    "ProtocolError",
    "StorageError",
    "TransportError",
    "ValidationError",
    "bind_context",
    "clear_context",
    "configure_logging",
    "current_context",
    "from_micros",
    "get_logger",
    "log_context",
    "new_id",
    "now_micros",
    "now_utc",
    "to_micros",
    "uuid7",
]
