"""Penyimpanan telemetry lokal.

InfluxDB adalah telemetry store resmi di edge. StorageWriter berada di antara
Collector dan InfluxDB, dan menjamin satu hal: kegagalan penyimpanan tidak
pernah menghentikan akuisisi.
"""

from fleetview_contracts import encode_record, encode_records
from fleetview_edge.storage.base import (
    StorageHealth,
    StorageRejectedError,
    StorageRetentionRejectedError,
    StorageState,
    StorageUnavailableError,
    TelemetryStore,
)
from fleetview_edge.storage.buffer import BoundedRetryBuffer
from fleetview_edge.storage.cardinality import (
    FORBIDDEN_TAG_KEYS,
    MAX_EXTRA_TAGS,
    estimate_series,
    verify_tag_keys,
)
from fleetview_edge.storage.influx import InfluxTelemetryStore
from fleetview_edge.storage.memory import InMemoryTelemetryStore
from fleetview_edge.storage.retention import RetentionPolicy
from fleetview_edge.storage.writer import StorageWriter

__all__ = [
    "FORBIDDEN_TAG_KEYS",
    "MAX_EXTRA_TAGS",
    "BoundedRetryBuffer",
    "InMemoryTelemetryStore",
    "InfluxTelemetryStore",
    "RetentionPolicy",
    "StorageHealth",
    "StorageRejectedError",
    "StorageRetentionRejectedError",
    "StorageState",
    "StorageUnavailableError",
    "StorageWriter",
    "TelemetryStore",
    "encode_record",
    "encode_records",
    "estimate_series",
    "verify_tag_keys",
]
