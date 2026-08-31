"""Satu titik telemetry — unit terkecil dalam sistem."""

from __future__ import annotations

import re
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from fleetview_contracts.enums import AcquisitionSource, Quality

__all__ = ["SLUG_PATTERN", "FieldValue", "Reading"]

SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")
"""Slug huruf kecil. Dipakai untuk sensor_id, measurement, dan nama field.

Dibatasi ketat karena nilai-nilai ini menjadi tag dan nama field InfluxDB.
Slug bebas adalah penyebab paling umum ledakan kardinalitas, jadi dicegah di
level contract — bukan sekadar lewat kesepakatan tim.
"""

FieldValue = float | int | bool | str
"""Tipe nilai yang boleh masuk sebagai field InfluxDB.

Bukan hanya `float`: LP-A104 punya 32 input digital bawaan (boolean), fungsi
counter (integer), dan bisa tersambung barcode reader (string). Mengunci ke float
sekarang akan memaksa perubahan format saat sinyal semacam itu muncul — mahal,
karena schema sudah beredar di 70 kapal.
"""


def _validate_slug(value: str, what: str) -> str:
    if not SLUG_PATTERN.match(value):
        raise ValueError(
            f"{what} tidak valid: {value!r}. Harus huruf kecil, angka, dan garis "
            f"bawah; diawali huruf/angka; maksimal 64 karakter."
        )
    return value


class Reading(BaseModel):
    """Satu titik data pada satu waktu, dari satu sensor.

    Memetakan langsung ke satu titik InfluxDB: `measurement` jadi measurement,
    `tags` jadi tag, `fields` jadi field, `ts` jadi timestamp.

    Sebuah Reading membawa **beberapa field**, bukan satu nilai tunggal. Ini
    disengaja: GPS harus mengirim lat/lon/sog/cog sebagai satu titik, kalau
    dipecah menjadi empat titik terpisah, lintang dan bujur bisa tidak konsisten
    saat salah satunya hilang. Untuk sensor skalar biasa, pakai `Reading.scalar()`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    seq: Annotated[int, Field(ge=0)]
    """Sequence monoton per kapal. **Ini patokan urutan, bukan `ts`.**

    Jam kapal bisa melenceng dan melompat mundur; sequence tidak bisa.
    """

    ts: Annotated[int, Field(ge=0)]
    """Waktu pengambilan, epoch mikrodetik UTC, menurut jam perangkat."""

    sensor_id: Annotated[str, Field(min_length=1, max_length=64)]
    """Slug stabil dari registry sensor. Bukan label bebas."""

    measurement: Annotated[str, Field(min_length=1, max_length=64)]
    """Besaran fisik. Lihat KNOWN_MEASUREMENTS untuk yang sudah dikenal."""

    fields: Annotated[dict[str, FieldValue], Field(min_length=1)]
    """Nilai terukur. Sensor skalar memakai kunci tunggal `value`."""

    unit: Annotated[str | None, Field(max_length=32)] = None
    """Satuan teknis, mis. `rpm`, `bar`, `degC`. None bila tidak berdimensi."""

    quality: Quality = Quality.GOOD
    source: AcquisitionSource = AcquisitionSource.LIVE

    tags: dict[str, str] = Field(default_factory=dict)
    """Tag tambahan berkardinalitas rendah, mis. `{"engine": "port"}`.

    ship_id dan device_id **tidak** ada di sini — keduanya ada di batch envelope
    dan ditempelkan saat penulisan ke InfluxDB, supaya tidak diulang ribuan kali
    di dalam satu payload.
    """

    @field_validator("sensor_id")
    @classmethod
    def _check_sensor_id(cls, v: str) -> str:
        return _validate_slug(v, "sensor_id")

    @field_validator("measurement")
    @classmethod
    def _check_measurement(cls, v: str) -> str:
        return _validate_slug(v, "measurement")

    @field_validator("fields")
    @classmethod
    def _check_field_names(cls, v: dict[str, FieldValue]) -> dict[str, FieldValue]:
        for name in v:
            _validate_slug(name, "nama field")
        return v

    @field_validator("tags")
    @classmethod
    def _check_tag_names(cls, v: dict[str, str]) -> dict[str, str]:
        for name in v:
            _validate_slug(name, "nama tag")
        return v

    @model_validator(mode="after")
    def _reject_reserved_tags(self) -> Self:
        reserved = {"ship_id", "device_id", "sensor_id"} & self.tags.keys()
        if reserved:
            raise ValueError(
                f"tag {sorted(reserved)} disediakan sistem dan ditambahkan saat "
                f"penulisan ke InfluxDB; jangan disetel di Reading."
            )
        return self

    @classmethod
    def scalar(
        cls,
        *,
        seq: int,
        ts: int,
        sensor_id: str,
        measurement: str,
        value: FieldValue,
        unit: str | None = None,
        quality: Quality = Quality.GOOD,
        source: AcquisitionSource = AcquisitionSource.LIVE,
        tags: dict[str, str] | None = None,
    ) -> Reading:
        """Pintasan untuk sensor bernilai tunggal — mayoritas kasus."""
        return cls(
            seq=seq,
            ts=ts,
            sensor_id=sensor_id,
            measurement=measurement,
            fields={"value": value},
            unit=unit,
            quality=quality,
            source=source,
            tags=tags or {},
        )
