"""Versi schema wire.

`SCHEMA_VERSION` ikut dikirim di setiap batch. Server memakainya untuk memutuskan
cara mem-parse payload, sehingga edge agent versi lama tetap bisa upload selama
masa rollout. Aturan perubahannya ada di contracts/CHANGELOG.md.
"""

from typing import Final

SCHEMA_VERSION: Final[str] = "1.0"
"""MAJOR.MINOR. MINOR naik untuk penambahan yang backward-compatible,
MAJOR naik untuk perubahan yang merusak kompatibilitas."""

__all__ = ["SCHEMA_VERSION"]
