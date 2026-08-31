"""Format wire bersama antara Edge Agent dan Central Platform.

Ini satu-satunya titik kopling antara edge dan central. Package ini tidak
bergantung pada apa pun selain Pydantic, dan tidak boleh mengimpor kode edge
maupun central.
"""

from fleetview_contracts.batch import BatchEnvelope, BatchPayload
from fleetview_contracts.enums import (
    KNOWN_MEASUREMENTS,
    AcquisitionSource,
    BatchStatus,
    ConnectionState,
    Quality,
    SyncPriority,
    Transport,
)
from fleetview_contracts.export import ExportedBatch, ExportManifest
from fleetview_contracts.heartbeat import EdgeHealth, Heartbeat, HeartbeatResponse
from fleetview_contracts.reading import FieldValue, Reading
from fleetview_contracts.sync import Ack, SyncState, UploadSession
from fleetview_contracts.telemetry import TelemetryRecord
from fleetview_contracts.version import SCHEMA_VERSION

__all__ = [
    "KNOWN_MEASUREMENTS",
    "SCHEMA_VERSION",
    "Ack",
    "AcquisitionSource",
    "BatchEnvelope",
    "BatchPayload",
    "BatchStatus",
    "ConnectionState",
    "EdgeHealth",
    "ExportManifest",
    "ExportedBatch",
    "FieldValue",
    "Heartbeat",
    "HeartbeatResponse",
    "Quality",
    "Reading",
    "SyncPriority",
    "SyncState",
    "TelemetryRecord",
    "Transport",
    "UploadSession",
]
