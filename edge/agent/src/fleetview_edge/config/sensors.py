"""Konfigurasi sensor.

Inilah yang menerjemahkan channel mentah perangkat menjadi telemetry yang
bermakna. Ia sengaja berupa **data, bukan kode**: satu build Edge Agent yang sama
dideploy ke 70+ kapal, dan tiap kapal punya mesin, sensor, dan pengkabelan
berbeda. Menambah sensor harus cukup dengan mengubah berkas YAML, bukan merilis
versi baru.

Ini juga yang membuat janji "sensor bisa bertambah di masa depan" jadi nyata.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from fleetview_common import ConfigError
from fleetview_contracts.reading import SLUG_PATTERN

__all__ = ["SensorConfig", "SensorRegistry", "ValidationRules", "load_sensor_registry"]


class ValidationRules(BaseModel):
    """Batas kewajaran untuk satu sensor.

    Semua opsional. Sensor tanpa aturan tetap dibaca dan disimpan — hanya saja
    kualitasnya tidak pernah bisa dinilai sebagai `suspect`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    min_value: float | None = None
    max_value: float | None = None
    max_rate_per_second: Annotated[float | None, Field(gt=0)] = None
    """Perubahan absolut maksimum per detik yang masih dianggap wajar.

    Berguna menangkap sensor yang mulai rusak: RPM tidak bisa melompat dari 700
    ke 7000 dalam satu detik, jadi kalau terjadi, kemungkinan besar itu gangguan
    listrik atau kabel longgar — bukan mesinnya.
    """

    stale_after_seconds: Annotated[float | None, Field(gt=0)] = None
    """Bila channel ini tidak terbaca selama sekian detik, tandai `stale`."""

    @model_validator(mode="after")
    def _check_bounds(self) -> Self:
        if (
            self.min_value is not None
            and self.max_value is not None
            and self.min_value > self.max_value
        ):
            raise ValueError(f"min_value ({self.min_value}) melebihi max_value ({self.max_value})")
        return self


class SensorConfig(BaseModel):
    """Satu sensor: dari mana dibaca, apa artinya, dan berapa batas wajarnya."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sensor_id: Annotated[str, Field(min_length=1, max_length=64)]
    """Slug stabil. Ini menjadi tag InfluxDB, jadi jangan diubah setelah dipakai —
    mengubahnya memutus riwayat data sensor tersebut."""

    channel: Annotated[str, Field(min_length=1, max_length=128)]
    """Pengenal asli di perangkat. Bentuknya bergantung adapter, mis.
    `UW65540` untuk LP-A104, `mock:rpm` untuk pengujian."""

    metric: Annotated[str, Field(min_length=1, max_length=64)]
    """Besaran fisik, mis. `rpm`, `temperature`. Boleh slug baru di luar
    KNOWN_MEASUREMENTS — sensor memang bisa bertambah."""

    unit: Annotated[str | None, Field(max_length=32)] = None

    field_name: Annotated[str, Field(min_length=1, max_length=64)] = "value"
    """Nama field di dalam record. Sensor skalar memakai `value`.

    GPS memakai beberapa sensor berbeda yang menunjuk metric `position` dengan
    field_name `lat`, `lon`, `sog`, `cog` — sehingga tergabung menjadi satu titik
    dan koordinatnya tidak bisa terpisah."""

    scale: float = 1.0
    offset: float = 0.0
    """Nilai teknis = mentah x scale + offset. Perangkat industri lazim mengirim
    bilangan bulat berskala, mis. 7425 untuk 742,5 rpm."""

    poll_interval_seconds: Annotated[float, Field(gt=0)] = 1.0
    enabled: bool = True

    tags: dict[str, str] = Field(default_factory=dict)
    """Tag berkardinalitas rendah, mis. `{"engine": "port"}`."""

    validation: ValidationRules = Field(default_factory=ValidationRules)

    @field_validator("sensor_id", "metric", "field_name")
    @classmethod
    def _check_slug(cls, v: str) -> str:
        if not SLUG_PATTERN.match(v):
            raise ValueError(f"{v!r} bukan slug valid: harus huruf kecil, angka, dan garis bawah")
        return v

    @field_validator("tags")
    @classmethod
    def _check_tags(cls, v: dict[str, str]) -> dict[str, str]:
        reserved = {"ship_id", "device_id", "sensor_id"} & v.keys()
        if reserved:
            raise ValueError(f"tag {sorted(reserved)} disediakan sistem, jangan disetel di sini")
        for name in v:
            if not SLUG_PATTERN.match(name):
                raise ValueError(f"nama tag {name!r} bukan slug valid")
        return v

    def apply_scaling(self, raw: float | int) -> float:
        return float(raw) * self.scale + self.offset


class SensorRegistry:
    """Kumpulan sinyal sensor, terindeks berdasarkan channel dan sensor_id.

    Satu entri config = satu sinyal (satu channel -> satu field). Beberapa entri
    yang berbagi `sensor_id` membentuk **satu record** — itulah cara GPS
    mengirim lat/lon/sog/cog sebagai satu titik.

    Karena itu yang wajib unik adalah pasangan `(sensor_id, field_name)`, bukan
    `sensor_id` saja.
    """

    def __init__(self, sensors: list[SensorConfig]) -> None:
        seen: set[tuple[str, str]] = set()
        by_id: dict[str, list[SensorConfig]] = {}
        by_channel: dict[str, list[SensorConfig]] = {}

        for s in sensors:
            key = (s.sensor_id, s.field_name)
            if key in seen:
                raise ConfigError(
                    f"sinyal ganda: sensor_id {s.sensor_id!r} field {s.field_name!r} "
                    f"muncul lebih dari sekali",
                    code="config.duplicate_signal",
                    details={"sensor_id": s.sensor_id, "field_name": s.field_name},
                )
            seen.add(key)
            by_id.setdefault(s.sensor_id, []).append(s)
            by_channel.setdefault(s.channel, []).append(s)

        # Entri yang berbagi sensor_id berakhir di satu record, dan record itu
        # mengambil metric/unit/tag dari salah satunya. Kalau mereka tidak
        # sepakat, hasilnya record yang isinya bergantung urutan pembacaan —
        # bug yang sangat sulit dilacak. Lebih baik ditolak saat memuat config.
        for sensor_id, group in by_id.items():
            metrics = {s.metric for s in group}
            if len(metrics) > 1:
                raise ConfigError(
                    f"sensor_id {sensor_id!r} dipakai dengan metric berbeda: {sorted(metrics)}. "
                    f"Entri yang berbagi sensor_id membentuk satu record, jadi metric-nya "
                    f"harus sama.",
                    code="config.inconsistent_signal_group",
                    details={"sensor_id": sensor_id, "metrics": sorted(metrics)},
                )

        self._by_id = by_id
        self._by_channel = by_channel
        self._all = list(sensors)

    def __len__(self) -> int:
        return len(self._all)

    def __iter__(self) -> Iterator[SensorConfig]:
        return iter(self._all)

    @property
    def enabled(self) -> list[SensorConfig]:
        return [s for s in self._all if s.enabled]

    def by_id(self, sensor_id: str) -> list[SensorConfig]:
        """Semua sinyal yang membentuk record untuk sensor_id ini.

        Sensor skalar mengembalikan satu entri; GPS mengembalikan empat.
        """
        return list(self._by_id.get(sensor_id, []))

    @property
    def sensor_ids(self) -> set[str]:
        return set(self._by_id)

    def for_channel(self, channel: str) -> list[SensorConfig]:
        """Sensor yang membaca dari channel ini.

        Bisa lebih dari satu: satu register perangkat kadang memuat beberapa
        besaran, dan beberapa sensor bisa menunjuk channel yang sama dengan
        skala berbeda.
        """
        return [s for s in self._by_channel.get(channel, []) if s.enabled]

    @property
    def channels(self) -> set[str]:
        return {s.channel for s in self._all if s.enabled}


def load_sensor_registry(path: Path) -> SensorRegistry:
    """Muat konfigurasi sensor dari YAML.

    Raises:
        ConfigError: berkas tidak ada, YAML rusak, atau config tidak valid.
    """
    if not path.is_file():
        raise ConfigError(
            f"berkas config sensor tidak ditemukan: {path}",
            code="config.sensors_missing",
            details={"path": str(path)},
        )
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(
            f"YAML rusak di {path}: {exc}",
            code="config.yaml_invalid",
            details={"path": str(path)},
        ) from exc

    if not isinstance(raw, dict) or "sensors" not in raw:
        raise ConfigError(
            f"{path} harus berupa mapping dengan kunci 'sensors'",
            code="config.sensors_invalid",
            details={"path": str(path)},
        )

    try:
        sensors = [SensorConfig(**item) for item in raw["sensors"]]
    except Exception as exc:
        raise ConfigError(
            f"config sensor tidak valid di {path}: {exc}",
            code="config.sensors_invalid",
            details={"path": str(path)},
        ) from exc

    return SensorRegistry(sensors)
