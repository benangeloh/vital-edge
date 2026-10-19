"""Katalog jenis sensor yang dikenal.

Teknisi memilih jenis dari daftar ini, bukan mengisi besaran, satuan, skala, dan
batas wajar satu per satu. Isinya hanya yang sudah terdokumentasi di
docs/hardware/konvensi-atlogic.md; jenis lain ditambah saat sudah ada dasarnya,
bukan ditebak.

`verified` jujur soal skala: hanya yang sudah dicocokkan ke layar HMI bertanda
True. Skala yang diasumsikan 1:1 ditandai supaya tidak ada yang mengira sudah
dipastikan.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fleetview_common import ValidationError

__all__ = ["PRESETS", "SensorPreset", "get_preset"]


@dataclass(frozen=True, slots=True)
class SensorPreset:
    id: str
    label: str
    metric: str
    unit: str | None
    scale: float
    name_hint: str
    """Saran nama sensor. Hanya saran: nama menjadi tag di basis data dan
    teknisi yang tahu mesin mana yang dimaksud."""
    verified: bool
    note: str | None = None
    min_value: float | None = None
    max_value: float | None = None

    def as_entry(self) -> dict[str, Any]:
        """Isian registry dari preset ini, tanpa identitas sensor dan alamatnya."""
        entry: dict[str, Any] = {"metric": self.metric, "unit": self.unit, "scale": self.scale}
        limits = {
            k: v
            for k, v in (("min_value", self.min_value), ("max_value", self.max_value))
            if v is not None
        }
        if limits:
            entry["validation"] = limits
        return entry

    def as_dict(self) -> dict[str, Any]:
        """Untuk templat Console."""
        return {
            "id": self.id,
            "label": self.label,
            "name_hint": self.name_hint,
            "unit": self.unit,
            "scale": self.scale,
            "verified": self.verified,
            "note": self.note,
        }


_BELUM_DICOCOKKAN = "Skala 1:1 masih asumsi; cocokkan ke layar HMI saat mesin berjalan."

PRESETS: tuple[SensorPreset, ...] = (
    SensorPreset(
        id="rpm",
        label="RPM mesin",
        metric="rpm",
        unit="rpm",
        scale=1.0,
        name_hint="me_rpm",
        verified=False,
        note=_BELUM_DICOCOKKAN,
        min_value=0.0,
        max_value=2000.0,
    ),
    SensorPreset(
        id="fw_temp",
        label="Suhu air tawar (F.W)",
        metric="temperature",
        unit="degC",
        scale=1.0,
        name_hint="fw_temp",
        verified=False,
        note=_BELUM_DICOCOKKAN,
    ),
    SensorPreset(
        id="lo_press",
        label="Tekanan oli pelumas (L.O)",
        metric="pressure",
        unit="bar",
        scale=0.1,
        name_hint="lo_press",
        verified=True,
        note="Skala 0,1 sudah dicocokkan ke HMI (31 mentah tampil 3,1 bar).",
        min_value=0.0,
        max_value=10.0,
    ),
)

_BY_ID = {p.id: p for p in PRESETS}


def get_preset(preset_id: str) -> SensorPreset:
    try:
        return _BY_ID[preset_id]
    except KeyError:
        raise ValidationError(
            "pilih jenis sensor dari daftar",
            code="sensor.preset_unknown",
            details={"jenis": preset_id},
        ) from None
