"""Generator telemetry sintetis skala armada.

Menghasilkan data untuk N kapal x M sensor pada laju tertentu, dengan bentuk
yang menyerupai telemetry kapal sungguhan — bukan angka acak.

Kenapa bentuknya penting untuk benchmark: ukuran penyimpanan dan rasio kompresi
sangat bergantung pada isi data. Angka acak tidak terkompresi; nilai yang
berubah perlahan seperti telemetry sungguhan terkompresi sangat baik. Benchmark
yang memakai angka acak akan melaporkan kebutuhan disk beberapa kali lipat dari
yang sebenarnya, dan keputusan kapasitas yang diambil darinya akan salah.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from uuid import UUID, uuid5

from fleetview_common import now_micros
from fleetview_contracts import AcquisitionSource, Quality, TelemetryRecord

__all__ = ["FleetGenerator", "SensorProfile", "ShipGenerator", "default_profiles"]

#: Namespace tetap supaya ship_id sama di setiap kali simulator dijalankan —
#: benchmark yang bisa diulang butuh identitas yang stabil.
_NS = UUID("6f1a9a1e-0000-4000-8000-000000000000")


@dataclass(frozen=True, slots=True)
class SensorProfile:
    """Bentuk satu sinyal: nilai dasar, ayunan, derau, dan satuan."""

    sensor_id: str
    metric: str
    unit: str | None
    base: float
    swing: float
    noise: float
    period_seconds: float = 300.0
    tags: dict[str, str] = field(default_factory=dict)


def default_profiles(count: int) -> list[SensorProfile]:
    """Susun `count` sinyal yang menyerupai instrumentasi kapal sungguhan.

    Empat besaran inti diulang untuk beberapa mesin dan titik ukur, persis
    seperti kapal nyata: satu kapal punya banyak sensor suhu dan tekanan di
    titik berbeda, bukan satu dari tiap jenis.
    """
    templates = [
        ("rpm", "rpm", 1450.0, 60.0, 8.0),
        ("engine_load", "percent", 76.0, 8.0, 1.5),
        ("temperature", "degC", 412.0, 25.0, 3.0),
        ("pressure", "bar", 4.2, 0.4, 0.05),
        ("fuel_flow", "l_per_hour", 186.0, 20.0, 4.0),
        ("fuel_level", "percent", 78.0, 3.0, 0.2),
        ("voltage", "volt", 24.3, 0.4, 0.08),
    ]
    engines = ("port", "stbd", "aux1", "aux2")

    profiles: list[SensorProfile] = []
    index = 0
    while len(profiles) < count:
        metric, unit, base, swing, noise = templates[index % len(templates)]
        engine = engines[(index // len(templates)) % len(engines)]
        point = index // (len(templates) * len(engines))
        profiles.append(
            SensorProfile(
                sensor_id=f"{engine}_{metric}_{point:02d}",
                metric=metric,
                unit=unit,
                base=base,
                swing=swing,
                noise=noise,
                period_seconds=180.0 + (index % 7) * 45.0,
                tags={"engine": engine},
            )
        )
        index += 1
    return profiles[:count]


class ShipGenerator:
    """Menghasilkan telemetry untuk satu kapal."""

    def __init__(
        self,
        *,
        slug: str,
        profiles: list[SensorProfile],
        seed: int = 0,
        suspect_rate: float = 0.001,
    ) -> None:
        self.slug = slug
        self.ship_id = uuid5(_NS, f"ship:{slug}")
        self.device_id = uuid5(_NS, f"device:{slug}")
        self._profiles = profiles
        self._rng = random.Random(seed)
        self._suspect_rate = suspect_rate
        self._tick = 0
        self._sequence = 0

    @property
    def sensor_count(self) -> int:
        return len(self._profiles)

    def tick(self, *, timestamp_us: int | None = None) -> list[TelemetryRecord]:
        """Hasilkan satu putaran: satu record per sensor."""
        self._tick += 1
        stamp = timestamp_us if timestamp_us is not None else now_micros()
        records: list[TelemetryRecord] = []

        for profile in self._profiles:
            phase = 2 * math.pi * (self._tick / max(1.0, profile.period_seconds))
            value = (
                profile.base + profile.swing * math.sin(phase) + self._rng.gauss(0.0, profile.noise)
            )

            # Sebagian kecil pembacaan ditandai suspect, seperti di lapangan.
            # Ini bukan hiasan: record dengan quality berbeda menghasilkan seri
            # InfluxDB terpisah, dan itu ikut menentukan kardinalitas.
            quality = Quality.SUSPECT if self._rng.random() < self._suspect_rate else Quality.GOOD

            self._sequence += 1
            records.append(
                TelemetryRecord(
                    ship_id=self.ship_id,
                    device_id=self.device_id,
                    sensor_id=profile.sensor_id,
                    sequence_number=self._sequence,
                    timestamp=stamp,
                    metric=profile.metric,
                    values={"value": round(value, 2)},
                    unit=profile.unit,
                    quality=quality,
                    source=AcquisitionSource.SIMULATED,
                    tags=dict(profile.tags),
                )
            )
        return records


class FleetGenerator:
    """Menghasilkan telemetry untuk seluruh armada."""

    def __init__(
        self,
        *,
        ships: int = 70,
        sensors_per_ship: int = 100,
        seed: int = 1,
        suspect_rate: float = 0.001,
    ) -> None:
        profiles = default_profiles(sensors_per_ship)
        self.ships = [
            ShipGenerator(
                slug=f"SHIP-{i + 1:03d}",
                profiles=profiles,
                seed=seed + i,
                suspect_rate=suspect_rate,
            )
            for i in range(ships)
        ]
        self.sensors_per_ship = sensors_per_ship

    @property
    def points_per_tick(self) -> int:
        return len(self.ships) * self.sensors_per_ship

    def tick(self, *, timestamp_us: int | None = None) -> list[TelemetryRecord]:
        stamp = timestamp_us if timestamp_us is not None else now_micros()
        records: list[TelemetryRecord] = []
        for ship in self.ships:
            records.extend(ship.tick(timestamp_us=stamp))
        return records
