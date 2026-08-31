"""Adapter perangkat lapangan.

Tidak ada bagian di luar package ini yang boleh tahu bahwa register address itu
ada. Itulah yang membuat jalur integrasi LP-A104 masih bisa diputuskan belakangan
tanpa menyandera sisa sistem.
"""

from fleetview_edge.protocol.base import (
    AdapterHealth,
    LinkState,
    ProtocolAdapter,
    ProtocolNotConnectedError,
    ProtocolTimeoutError,
    RawPoint,
)
from fleetview_edge.protocol.lp_a104 import LPA104Adapter
from fleetview_edge.protocol.mock import MockLPAAdapter, MockScript
from fleetview_edge.protocol.simulator import EnginePhase, SimulatorAdapter

__all__ = [
    "AdapterHealth",
    "EnginePhase",
    "LPA104Adapter",
    "LinkState",
    "MockLPAAdapter",
    "MockScript",
    "ProtocolAdapter",
    "ProtocolNotConnectedError",
    "ProtocolTimeoutError",
    "RawPoint",
    "SimulatorAdapter",
]
