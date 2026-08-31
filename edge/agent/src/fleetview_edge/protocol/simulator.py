"""SimulatorAdapter — profil mesin kapal yang wajar.

Berbeda dari MockLPAAdapter yang mengutamakan sifat bisa diprediksi, adapter ini
mengutamakan kewajaran. Ia dipakai untuk pengembangan, demo, dan uji beban skala
armada — hal-hal yang butuh data terlihat seperti kapal sungguhan, bukan seperti
angka tetap.

Nilainya **tidak diklaim akurat secara teknis.** Ini bukan model termodinamika;
ini gerakan yang masuk akal supaya grafik terlihat hidup dan supaya aturan
validasi bisa diuji. Setiap record yang dihasilkan diberi
`AcquisitionSource.SIMULATED`, sehingga data simulasi tidak akan pernah tertukar
dengan data sungguhan di hilir.
"""

from __future__ import annotations

import math
import random
from enum import StrEnum

from fleetview_common import now_micros
from fleetview_edge.protocol.base import (
    AdapterHealth,
    LinkState,
    ProtocolAdapter,
    ProtocolNotConnectedError,
    RawPoint,
)

__all__ = ["EnginePhase", "SimulatorAdapter"]


class EnginePhase(StrEnum):
    """Kondisi operasi kapal yang ditiru."""

    STOPPED = "stopped"
    IDLE = "idle"
    MANOEUVRING = "manoeuvring"
    CRUISE = "cruise"


# Target per fase: (rpm, engine_load %, exhaust temp C)
_PHASE_TARGETS: dict[EnginePhase, tuple[float, float, float]] = {
    EnginePhase.STOPPED: (0.0, 0.0, 30.0),
    EnginePhase.IDLE: (620.0, 12.0, 180.0),
    EnginePhase.MANOEUVRING: (900.0, 45.0, 320.0),
    EnginePhase.CRUISE: (1450.0, 78.0, 415.0),
}


class SimulatorAdapter(ProtocolAdapter):
    """Menghasilkan telemetry mesin kapal yang bergerak wajar.

    Args:
        phase: kondisi operasi yang ditiru.
        seed: benih acak. Diberi nilai agar hasilnya bisa diulang di test.
        noise: amplitudo derau relatif, 0 berarti mulus sempurna.
        fuel_start_percent: level tangki awal; menurun perlahan seiring waktu.
    """

    name = "simulator"

    #: Channel yang disajikan. Konfigurasi sensor memetakan ini ke sensor.
    CHANNELS = (
        "sim:me_port_rpm",
        "sim:me_port_load",
        "sim:me_port_exhaust_temp",
        "sim:me_port_lube_pressure",
        "sim:fuel_level",
        "sim:fuel_flow",
        "sim:bus_voltage",
        "sim:gps_lat",
        "sim:gps_lon",
        "sim:gps_sog",
        "sim:gps_cog",
    )

    def __init__(
        self,
        *,
        phase: EnginePhase = EnginePhase.CRUISE,
        seed: int | None = None,
        noise: float = 0.02,
        fuel_start_percent: float = 84.0,
    ) -> None:
        self.phase = phase
        self._rng = random.Random(seed)
        self._noise = noise
        self._connected = False
        self._tick = 0
        self._fuel = fuel_start_percent
        self._lat = -7.2100
        self._lon = 112.7300
        self._connected_since_us: int | None = None
        self._last_success_us: int | None = None

    def set_phase(self, phase: EnginePhase) -> None:
        """Ubah kondisi operasi di tengah jalan."""
        self.phase = phase

    def _jitter(self, value: float) -> float:
        if self._noise <= 0 or value == 0:
            return value
        return value * (1.0 + self._rng.uniform(-self._noise, self._noise))

    async def connect(self) -> None:
        self._connected = True
        self._connected_since_us = now_micros()

    async def read_points(self) -> list[RawPoint]:
        if not self._connected:
            raise ProtocolNotConnectedError("simulator: belum tersambung")

        self._tick += 1
        now = now_micros()
        rpm_t, load_t, exh_t = _PHASE_TARGETS[self.phase]

        # Gelombang lambat supaya grafik terlihat hidup, bukan datar.
        wave = math.sin(self._tick / 45.0)

        rpm = self._jitter(rpm_t + wave * rpm_t * 0.03)
        load = max(0.0, min(100.0, self._jitter(load_t + wave * 4.0)))
        exhaust = self._jitter(exh_t + wave * 12.0)
        lube = 0.0 if self.phase is EnginePhase.STOPPED else self._jitter(4.2 + wave * 0.15)
        flow = 0.0 if self.phase is EnginePhase.STOPPED else self._jitter(load * 2.4)
        voltage = self._jitter(24.3)

        if self.phase is not EnginePhase.STOPPED:
            self._fuel = max(0.0, self._fuel - flow * 1e-5)
            sog = self._jitter(rpm / 100.0)
            self._lat += sog * 1e-6
            self._lon += sog * 1.4e-6
        else:
            sog = 0.0

        cog = (87.0 + wave * 3.0) % 360.0
        self._last_success_us = now

        values: dict[str, float] = {
            "sim:me_port_rpm": round(rpm, 1),
            "sim:me_port_load": round(load, 1),
            "sim:me_port_exhaust_temp": round(exhaust, 1),
            "sim:me_port_lube_pressure": round(lube, 2),
            "sim:fuel_level": round(self._fuel, 2),
            "sim:fuel_flow": round(flow, 2),
            "sim:bus_voltage": round(voltage, 2),
            "sim:gps_lat": round(self._lat, 6),
            "sim:gps_lon": round(self._lon, 6),
            "sim:gps_sog": round(sog, 2),
            "sim:gps_cog": round(cog, 1),
        }
        return [RawPoint(channel=ch, value=v, read_at_us=now) for ch, v in values.items()]

    async def health(self) -> AdapterHealth:
        return AdapterHealth(
            link=LinkState.CONNECTED if self._connected else LinkState.DISCONNECTED,
            connected_since_us=self._connected_since_us,
            last_success_us=self._last_success_us,
            counters={"ticks": self._tick},
            detail=f"simulasi fase {self.phase.value}",
        )

    async def close(self) -> None:
        self._connected = False
        self._connected_since_us = None
