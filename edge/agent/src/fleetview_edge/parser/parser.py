"""Mengubah pembacaan mentah perangkat menjadi telemetry yang bermakna.

Parser adalah tempat channel mentah bertemu konfigurasi sensor. Ia menerapkan
skala, menempelkan satuan dan tag, lalu menggabungkan sensor multi-field
(seperti GPS) menjadi satu record.

Parser sengaja **tidak menilai kualitas** — itu tugas Validator. Pemisahan ini
membuat keduanya bisa diuji sendiri-sendiri, dan membuat jelas bahwa gagal
mengurai (channel tidak dikenal, tipe salah) berbeda dari nilai yang berhasil
diurai tapi mencurigakan.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from fleetview_common import get_logger
from fleetview_edge.config.sensors import SensorConfig, SensorRegistry
from fleetview_edge.protocol.base import RawPoint

__all__ = ["ParseResult", "ParsedValue", "TelemetryParser"]

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ParsedValue:
    """Satu nilai yang sudah diskalakan, terikat ke konfigurasi sensornya."""

    sensor: SensorConfig
    field_name: str
    value: float | int | bool | str
    read_at_us: int


@dataclass
class ParseResult:
    """Hasil satu putaran pengurai."""

    values: list[ParsedValue] = field(default_factory=list)
    unknown_channels: set[str] = field(default_factory=set)
    """Channel yang dikembalikan perangkat tetapi tidak ada di registry.

    Ini **tidak dianggap error**. Perangkat lapangan bisa menyajikan lebih banyak
    channel daripada yang kita konfigurasikan, dan sensor bisa ditambahkan di
    perangkat sebelum config diperbarui. Channel semacam ini dilaporkan supaya
    operator tahu ada data yang belum dimanfaatkan, bukan supaya dianggap gagal.
    """
    failed: dict[str, str] = field(default_factory=dict)
    """channel -> alasan, untuk nilai yang tidak bisa diurai sama sekali."""


class TelemetryParser:
    """Menerjemahkan RawPoint menjadi nilai bersensor, memakai registry."""

    def __init__(self, registry: SensorRegistry) -> None:
        self._registry = registry
        self._warned_unknown: set[str] = set()

    def parse(self, points: list[RawPoint]) -> ParseResult:
        result = ParseResult()

        for point in points:
            sensors = self._registry.for_channel(point.channel)
            if not sensors:
                result.unknown_channels.add(point.channel)
                # Diperingatkan sekali per channel, bukan setiap putaran —
                # pada 1 Hz, memperingatkan tiap kali akan membanjiri log kapal
                # dengan jutaan baris identik.
                if point.channel not in self._warned_unknown:
                    self._warned_unknown.add(point.channel)
                    log.info(
                        "parser.unknown_channel",
                        channel=point.channel,
                        note="perangkat menyajikan channel yang tidak ada di config sensor",
                    )
                continue

            for sensor in sensors:
                parsed = self._parse_one(point, sensor)
                if parsed is None:
                    result.failed[point.channel] = (
                        f"nilai non-finite (NaN/inf) setelah skala untuk sensor {sensor.sensor_id}"
                    )
                    continue
                result.values.append(parsed)

        return result

    def _parse_one(self, point: RawPoint, sensor: SensorConfig) -> ParsedValue | None:
        value: float | int | bool | str

        if isinstance(point.value, bool):
            # Diperiksa sebelum int, karena bool adalah subclass int di Python.
            # Menskalakan sebuah boolean tidak bermakna; input digital dibiarkan
            # apa adanya.
            value = point.value
        elif isinstance(point.value, int | float):
            scaled = sensor.apply_scaling(point.value)
            if not math.isfinite(scaled):
                # NaN dan infinity harus dihentikan di sini. Kalau lolos, mereka
                # meracuni setiap rata-rata, minimum, dan maksimum di hilir tanpa
                # menimbulkan error — grafiknya kosong dan tidak ada yang tahu
                # kenapa. Perangkat lapangan memang kadang mengirim NaN sebagai
                # penanda "tidak terukur".
                return None
            value = scaled
        else:
            # Nilai teks (mis. barcode) tidak diskalakan.
            value = point.value

        return ParsedValue(
            sensor=sensor,
            field_name=sensor.field_name,
            value=value,
            read_at_us=point.read_at_us,
        )
