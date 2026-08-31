"""Konfigurasi retensi bucket InfluxDB.

Retensi di edge adalah keputusan operasional, bukan sekadar setelan. Kapal punya
disk terbatas dan bisa offline berminggu-minggu; menyimpan data mentah 1 Hz
selamanya akan memenuhi disk dan menghentikan akuisisi — kegagalan yang justru
paling merugikan, karena terjadi diam-diam.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

__all__ = ["RetentionPolicy"]

SECONDS_PER_DAY: Final[int] = 86_400


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    """Berapa lama telemetry disimpan di InfluxDB lokal.

    Args:
        days: umur simpan. 0 berarti tak terbatas — **tidak dianjurkan di edge**,
            karena disk yang penuh menghentikan akuisisi.
        shard_group_duration_hours: opsional. Shard yang lebih kecil membuat
            penghapusan data kedaluwarsa lebih murah, dengan biaya lebih banyak
            berkas. Biarkan None kecuali ada alasan jelas.
    """

    days: int = 90
    shard_group_duration_hours: int | None = None

    def __post_init__(self) -> None:
        if self.days < 0:
            raise ValueError(f"retensi tidak boleh negatif, dapat {self.days}")
        if self.shard_group_duration_hours is not None and self.shard_group_duration_hours <= 0:
            raise ValueError("shard_group_duration_hours harus positif")

    @property
    def seconds(self) -> int:
        """Detik retensi. 0 berarti tak terbatas, sesuai konvensi InfluxDB."""
        return self.days * SECONDS_PER_DAY

    @property
    def unlimited(self) -> bool:
        return self.days == 0

    def to_bucket_rules(self) -> list[dict[str, Any]]:
        """Bentuk `retentionRules` untuk API bucket InfluxDB v2.

        Daftar kosong berarti tak terbatas.
        """
        if self.unlimited:
            return []
        rule: dict[str, Any] = {"type": "expire", "everySeconds": self.seconds}
        if self.shard_group_duration_hours is not None:
            rule["shardGroupDurationSeconds"] = self.shard_group_duration_hours * 3600
        return [rule]
