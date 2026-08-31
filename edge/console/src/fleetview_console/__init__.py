"""Edge Console — UI operasional ringan yang disajikan Edge Agent.

Alat pemeliharaan, bukan dashboard analytics. Berjalan di dalam proses agent
yang sama; lihat keputusan D4 di docs/architecture/00-assessment.md.
"""

from fleetview_console.app import create_console_app
from fleetview_console.context import (
    ConsoleContext,
    ExportTarget,
    LogEntry,
    NetworkLink,
    SensorState,
    SyncSnapshot,
    SystemSnapshot,
)
from fleetview_console.metrics import SystemMetrics
from fleetview_console.security import redact_config

__all__ = [
    "ConsoleContext",
    "ExportTarget",
    "LogEntry",
    "NetworkLink",
    "SensorState",
    "SyncSnapshot",
    "SystemMetrics",
    "SystemSnapshot",
    "create_console_app",
    "redact_config",
]
