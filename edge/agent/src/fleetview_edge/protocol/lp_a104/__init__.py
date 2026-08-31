"""Kode spesifik Autonics LP-A104.

Semua hal yang menyentuh detail perangkat berada di dalam sub-package ini.
Tidak ada bagian di luar sini yang boleh tahu bahwa alamat UW itu ada.
"""

from fleetview_edge.protocol.lp_a104.adapter import LPA104Adapter
from fleetview_edge.protocol.lp_a104.addressing import (
    CanArea,
    DeviceArea,
    UwRange,
    can_module_range,
    parse_ub_address,
    ub_address,
    uw_area_of,
)
from fleetview_edge.protocol.lp_a104.serial_params import FlowControl, Parity, SerialParams

__all__ = [
    "CanArea",
    "DeviceArea",
    "FlowControl",
    "LPA104Adapter",
    "Parity",
    "SerialParams",
    "UwRange",
    "can_module_range",
    "parse_ub_address",
    "ub_address",
    "uw_area_of",
]
