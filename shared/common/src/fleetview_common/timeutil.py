"""Utilitas waktu.

Seluruh sistem memakai **epoch mikrodetik UTC** untuk timestamp telemetry, dan
datetime yang selalu membawa timezone di batas API. Datetime naif dilarang: jam
kapal melenceng dan melompat, dan timestamp tanpa zona waktu membuat kondisi itu
mustahil dipahami setelah kejadian.
"""

from __future__ import annotations

from datetime import UTC, datetime

__all__ = ["from_micros", "now_micros", "now_utc", "to_micros"]


def now_utc() -> datetime:
    """Waktu sekarang, selalu sadar timezone, selalu UTC."""
    return datetime.now(UTC)


def now_micros() -> int:
    """Waktu sekarang sebagai epoch mikrodetik UTC."""
    return to_micros(now_utc())


def to_micros(dt: datetime) -> int:
    """Ubah datetime sadar-timezone menjadi epoch mikrodetik UTC.

    Raises:
        ValueError: bila `dt` naif. Menebak zona waktunya akan diam-diam merusak
            data dari kapal yang jamnya salah setel — persis kasus yang paling
            perlu kita deteksi.
    """
    if dt.tzinfo is None:
        raise ValueError(
            "datetime naif tidak bisa diubah ke epoch: zona waktunya ambigu. "
            "Pakai timeutil.now_utc() atau lampirkan tzinfo secara eksplisit."
        )
    return int(dt.timestamp() * 1_000_000)


def from_micros(micros: int) -> datetime:
    """Ubah epoch mikrodetik UTC menjadi datetime sadar-timezone."""
    return datetime.fromtimestamp(micros / 1_000_000, tz=UTC)
