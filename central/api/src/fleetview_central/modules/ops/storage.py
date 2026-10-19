"""Pemakaian penyimpanan host Central, untuk halaman Kesehatan Sistem.

Yang dibaca adalah filesystem tempat proses API berjalan, bukan volume Postgres
atau InfluxDB di container lain. Di deployment Docker satu host keduanya hidup di
disk fisik yang sama, jadi angkanya tetap berguna — tetapi namanya harus jujur:
ini "disk host", dan path yang diperiksa ikut dilaporkan supaya tidak ada yang
mengira itu partisi data.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

__all__ = ["DiskUsage", "StorageLevel", "classify", "read_disk_usage"]


class StorageLevel(StrEnum):
    OK = "ok"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class DiskUsage:
    path: str
    total_bytes: int
    used_bytes: int
    free_bytes: int
    used_percent: float
    level: StorageLevel

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "total_bytes": self.total_bytes,
            "used_bytes": self.used_bytes,
            "free_bytes": self.free_bytes,
            "used_percent": self.used_percent,
            "level": self.level.value,
        }


def classify(used_percent: float, *, warn: float, critical: float) -> StorageLevel:
    if used_percent >= critical:
        return StorageLevel.CRITICAL
    if used_percent >= warn:
        return StorageLevel.WARNING
    return StorageLevel.OK


def read_disk_usage(path: str, *, warn: float, critical: float) -> DiskUsage | None:
    """Pemakaian partisi yang memuat `path`, atau None bila tidak bisa dibaca.

    Tidak melempar: satu path yang salah di config tidak boleh membuat seluruh
    halaman kesehatan gagal, padahal pemeriksaan Postgres dan InfluxDB di
    sebelahnya masih berguna.
    """
    try:
        if not Path(path).exists():
            return None
        usage = shutil.disk_usage(path)
    except (OSError, ValueError):
        # ValueError: path berisi byte nol membuat exists() melempar.
        return None
    if usage.total <= 0:
        return None

    percent = round(100.0 * usage.used / usage.total, 1)
    return DiskUsage(
        path=path,
        total_bytes=usage.total,
        used_bytes=usage.used,
        free_bytes=usage.free,
        used_percent=percent,
        level=classify(percent, warn=warn, critical=critical),
    )
