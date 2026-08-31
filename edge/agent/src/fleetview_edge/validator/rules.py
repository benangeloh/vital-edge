"""Penilaian kualitas pembacaan.

Prinsip yang mengikat seluruh modul ini: **pembacaan yang buruk ditandai, tidak
pernah dibuang.**

Alasannya operasional. Kalau sensor rusak lalu datanya kita buang, yang terlihat
di pusat adalah ketiadaan data — persis sama dengan kapal yang sedang offline
atau agent yang mati. Ketiganya butuh tindakan yang sangat berbeda. Dengan
menyimpan nilainya beserta flag kualitas, operator bisa membedakan "sensor
temperatur nomor 3 rusak" dari "kapal ini belum menyetor data sejak Selasa".

Hal ini juga penting untuk analitik perawatan: pola nilai yang mencurigakan
sebelum kerusakan justru data yang paling berharga.
"""

from __future__ import annotations

from dataclasses import dataclass

from fleetview_common import get_logger
from fleetview_contracts import Quality
from fleetview_edge.config.sensors import SensorConfig

__all__ = ["SensorValidator", "ValidationOutcome"]

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ValidationOutcome:
    quality: Quality
    reason: str | None = None
    """Penjelasan singkat saat kualitas bukan `good`. Ikut ke log, bukan ke
    payload — payload hanya membawa flag kualitasnya."""


@dataclass
class _SensorState:
    """Yang perlu diingat untuk menilai pembacaan berikutnya."""

    last_value: float | None = None
    last_seen_us: int | None = None


class SensorValidator:
    """Menilai kualitas pembacaan berdasarkan aturan per sensor.

    Stateful: pemeriksaan laju perubahan dan staleness butuh nilai sebelumnya.
    Satu instance melayani seluruh sensor.
    """

    def __init__(self) -> None:
        self._state: dict[str, _SensorState] = {}

    def reset(self, sensor_id: str | None = None) -> None:
        """Lupakan riwayat. Dipanggil setelah sambungan pulih, supaya jeda
        panjang tidak salah dibaca sebagai lonjakan laju perubahan."""
        if sensor_id is None:
            self._state.clear()
        else:
            self._state.pop(sensor_id, None)

    def assess(
        self,
        sensor: SensorConfig,
        value: float | int | bool | str,
        read_at_us: int,
    ) -> ValidationOutcome:
        """Nilai satu pembacaan dan perbarui state internal."""
        state = self._state.setdefault(sensor.sensor_id, _SensorState())
        rules = sensor.validation

        # Nilai non-numerik (boolean digital, barcode) tidak punya konsep
        # rentang maupun laju perubahan. Kehadirannya saja sudah cukup.
        if isinstance(value, bool | str):
            state.last_seen_us = read_at_us
            return ValidationOutcome(Quality.GOOD)

        numeric = float(value)
        outcome = self._check_range(sensor, numeric) or self._check_rate(
            sensor, numeric, read_at_us, state
        )

        state.last_value = numeric
        state.last_seen_us = read_at_us

        if outcome is not None:
            log.debug(
                "validator.suspect_value",
                sensor_id=sensor.sensor_id,
                value=numeric,
                reason=outcome.reason,
            )
            return outcome

        _ = rules  # aturan sudah dipakai di helper di atas
        return ValidationOutcome(Quality.GOOD)

    def check_staleness(self, sensor: SensorConfig, now_us: int) -> ValidationOutcome | None:
        """Apakah sensor ini sudah terlalu lama tidak terbaca?

        Dipanggil untuk sensor yang **tidak** muncul di putaran polling terakhir.
        Mengembalikan None bila belum melewati batas atau bila tidak ada aturan.

        Staleness diukur dari kapan terakhir sensor ini **terbaca**, bukan dari
        kapan nilainya terakhir **berubah**. Nilai yang konstan itu wajar — mesin
        mati memang menghasilkan RPM 0 terus-menerus, dan itu bukan kerusakan.
        """
        threshold = sensor.validation.stale_after_seconds
        if threshold is None:
            return None

        state = self._state.get(sensor.sensor_id)
        if state is None or state.last_seen_us is None:
            return None

        age_s = (now_us - state.last_seen_us) / 1_000_000
        if age_s < threshold:
            return None

        return ValidationOutcome(
            Quality.STALE,
            f"tidak terbaca selama {age_s:.1f} detik (batas {threshold:.1f} detik)",
        )

    # -- helper -------------------------------------------------------------

    @staticmethod
    def _check_range(sensor: SensorConfig, value: float) -> ValidationOutcome | None:
        rules = sensor.validation
        if rules.min_value is not None and value < rules.min_value:
            return ValidationOutcome(
                Quality.SUSPECT, f"{value} di bawah batas bawah {rules.min_value}"
            )
        if rules.max_value is not None and value > rules.max_value:
            return ValidationOutcome(
                Quality.SUSPECT, f"{value} di atas batas atas {rules.max_value}"
            )
        return None

    @staticmethod
    def _check_rate(
        sensor: SensorConfig, value: float, read_at_us: int, state: _SensorState
    ) -> ValidationOutcome | None:
        max_rate = sensor.validation.max_rate_per_second
        if max_rate is None or state.last_value is None or state.last_seen_us is None:
            return None

        elapsed_s = (read_at_us - state.last_seen_us) / 1_000_000
        if elapsed_s <= 0:
            # Timestamp tidak maju — bisa terjadi saat jam melompat mundur.
            # Laju perubahan tidak bisa dihitung; jangan menuduh sensornya.
            return None

        rate = abs(value - state.last_value) / elapsed_s
        if rate <= max_rate:
            return None

        return ValidationOutcome(
            Quality.SUSPECT,
            f"berubah {rate:.1f} per detik (batas {max_rate:.1f}); "
            f"{state.last_value} -> {value} dalam {elapsed_s:.3f} detik",
        )
