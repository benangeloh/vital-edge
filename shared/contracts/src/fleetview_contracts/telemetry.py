"""Record telemetry lengkap — bentuk yang berdiri sendiri.

Ada dua bentuk untuk data yang sama, dan perbedaannya disengaja:

- `Reading` — bentuk **wire**. Tidak membawa ship_id/device_id, karena keduanya
  sudah ada di batch envelope. Satu batch berisi 500 pembacaan dari satu kapal;
  mengulang dua UUID sebanyak 500 kali adalah pemborosan yang percuma.
- `TelemetryRecord` — bentuk **lengkap**. Membawa identitas kapal dan perangkat,
  sehingga satu record bisa dibaca sendirian tanpa konteks tambahan.

Collector menghasilkan `TelemetryRecord`. Lapisan storage/sync (Phase 3–4) yang
memisahkan identitasnya ke envelope saat membentuk batch.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from fleetview_contracts.enums import AcquisitionSource, Quality
from fleetview_contracts.reading import FieldValue, Reading

__all__ = ["TelemetryRecord"]


class TelemetryRecord(BaseModel):
    """Satu pembacaan telemetry, lengkap dengan identitas asalnya."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ship_id: UUID
    device_id: UUID
    sensor_id: Annotated[str, Field(min_length=1, max_length=64)]

    sequence_number: Annotated[int, Field(ge=0)]
    """Monoton per kapal, tanpa celah. **Ini patokan urutan, bukan timestamp.**

    Jam kapal bisa melenceng dan melompat mundur; sequence tidak bisa.
    """

    timestamp: Annotated[int, Field(ge=0)]
    """Waktu pengambilan, epoch mikrodetik UTC, menurut jam perangkat."""

    metric: Annotated[str, Field(min_length=1, max_length=64)]
    """Besaran yang diukur, mis. `rpm`, `temperature`, `position`.

    Ini yang di InfluxDB disebut *measurement*; dinamai `metric` di sini karena
    itu istilah yang lebih lazim di luar konteks InfluxDB.
    """

    values: Annotated[dict[str, FieldValue], Field(min_length=1)]
    """Nilai terukur. Sensor skalar memakai kunci tunggal `value`; GPS memakai
    lat/lon/sog/cog dalam satu record supaya koordinatnya selalu konsisten."""

    unit: Annotated[str | None, Field(max_length=32)] = None
    quality: Quality = Quality.GOOD
    source: AcquisitionSource = AcquisitionSource.LIVE
    tags: dict[str, str] = Field(default_factory=dict)

    @property
    def value(self) -> FieldValue | None:
        """Nilai tunggal untuk sensor skalar, atau None bila multi-field.

        Mayoritas sensor bernilai tunggal, jadi ini yang paling sering dipakai.
        GPS sengaja mengembalikan None supaya pemanggil tidak diam-diam memakai
        salah satu koordinat saja.
        """
        if len(self.values) == 1 and "value" in self.values:
            return self.values["value"]
        return None

    def to_reading(self) -> Reading:
        """Bentuk wire yang ringkas. Identitas kapal dipindahkan ke envelope."""
        return Reading(
            seq=self.sequence_number,
            ts=self.timestamp,
            sensor_id=self.sensor_id,
            measurement=self.metric,
            fields=self.values,
            unit=self.unit,
            quality=self.quality,
            source=self.source,
            tags=self.tags,
        )

    @classmethod
    def from_reading(cls, reading: Reading, *, ship_id: UUID, device_id: UUID) -> TelemetryRecord:
        return cls(
            ship_id=ship_id,
            device_id=device_id,
            sensor_id=reading.sensor_id,
            sequence_number=reading.seq,
            timestamp=reading.ts,
            metric=reading.measurement,
            values=dict(reading.fields),
            unit=reading.unit,
            quality=reading.quality,
            source=reading.source,
            tags=dict(reading.tags),
        )
