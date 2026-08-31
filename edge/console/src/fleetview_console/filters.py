"""Filter Jinja untuk pemformatan operasional."""

from __future__ import annotations

import time
from typing import Any

from jinja2 import Environment

__all__ = ["register_filters"]


def relative_us(micros: int | None) -> str:
    """Waktu relatif ringkas dari epoch mikrodetik."""
    if not micros:
        return "—"
    seconds = max(0, int(time.time() - micros / 1_000_000))
    if seconds < 60:
        return f"{seconds} dtk"
    if seconds < 3600:
        return f"{seconds // 60} mnt"
    if seconds < 172_800:
        return f"{seconds // 3600} jam"
    return f"{seconds // 86_400} hr"


def duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    total = int(seconds)
    if total < 60:
        return f"{total} dtk"
    if total < 3600:
        return f"{total // 60} mnt"
    if total < 86_400:
        return f"{total // 3600} jam {(total % 3600) // 60} mnt"
    return f"{total // 86_400} hr {(total % 86_400) // 3600} jam"


def bytes_human(value: int | None) -> str:
    if value is None:
        return "—"
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def number(value: Any) -> str:
    """Pemisah ribuan gaya Indonesia. Angka besar tanpa pemisah sangat mudah
    salah baca — dan di halaman ini angka besar berarti data yang tertahan."""
    if value is None:
        return "—"
    try:
        return f"{int(value):,}".replace(",", ".")
    except (TypeError, ValueError):
        return str(value)


def percent(value: float | None) -> str:
    return "—" if value is None else f"{value:.0f}%"


def measure(value: float | None, unit: str = "") -> str:
    """None ditampilkan sebagai strip, bukan nol.

    Suhu 0 °C dan suhu yang tidak terbaca menuntut tindakan yang sangat berbeda.
    """
    if value is None:
        return "—"
    return f"{value:g}{unit}"


def register_filters(env: Environment) -> None:
    env.filters.update(
        {
            "relative_us": relative_us,
            "duration": duration,
            "bytes_human": bytes_human,
            "number": number,
            "percent": percent,
            "measure": measure,
        }
    )
