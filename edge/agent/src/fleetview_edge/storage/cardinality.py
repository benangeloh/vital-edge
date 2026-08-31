"""Penjagaan kardinalitas seri InfluxDB.

Ledakan kardinalitas adalah cara paling umum sebuah instalasi InfluxDB mati, dan
ia mati perlahan: penulisan makin lambat, memori naik, sampai akhirnya proses
di-OOM-kill. Di kapal, itu berarti kehilangan data tanpa ada yang menyadarinya
sampai berminggu-minggu kemudian.

Penyebabnya hampir selalu sama: sebuah nilai yang unik per pembacaan (timestamp,
sequence, UUID) tanpa sengaja dijadikan tag. Karena itu larangannya ditegakkan
di kode, bukan diserahkan pada kedisiplinan saat menulis config.
"""

from __future__ import annotations

from typing import Final

from fleetview_common import ValidationError

__all__ = [
    "FORBIDDEN_TAG_KEYS",
    "MAX_EXTRA_TAGS",
    "estimate_series",
    "verify_tag_keys",
]

FORBIDDEN_TAG_KEYS: Final[frozenset[str]] = frozenset(
    {
        # Unik per pembacaan — masing-masing akan membuat satu seri baru,
        # setiap detik, selamanya.
        "timestamp",
        "time",
        "ts",
        "seq",
        "sequence",
        "sequence_number",
        "batch_id",
        "export_id",
        "session_id",
        # Ini nilai terukur, bukan dimensi identitas.
        "value",
        "reading",
        # Berpotensi tak terbatas.
        "message",
        "error",
        "detail",
        "reason",
    }
)

MAX_EXTRA_TAGS: Final[int] = 8
"""Batas tag tambahan per sensor, di luar tag identitas.

Bukan batas teknis InfluxDB, melainkan pagar akal sehat: sensor yang butuh lebih
dari delapan dimensi hampir pasti sedang menyelundupkan data pengukuran ke dalam
tag.
"""


def verify_tag_keys(tags: dict[str, str], *, context: str = "") -> None:
    """Tolak kunci tag yang akan meledakkan kardinalitas.

    Raises:
        ValidationError: ada kunci terlarang, atau tag tambahan terlalu banyak.
    """
    forbidden = sorted(FORBIDDEN_TAG_KEYS & {k.lower() for k in tags})
    if forbidden:
        raise ValidationError(
            f"kunci tag {forbidden} dilarang{f' pada {context}' if context else ''}: "
            f"nilainya unik per pembacaan dan akan membuat satu seri InfluxDB baru "
            f"setiap detik. Simpan sebagai field, bukan tag.",
            code="storage.forbidden_tag_key",
            details={"forbidden": forbidden, "context": context},
        )

    if len(tags) > MAX_EXTRA_TAGS:
        raise ValidationError(
            f"{len(tags)} tag tambahan melebihi batas {MAX_EXTRA_TAGS}"
            f"{f' pada {context}' if context else ''}. Sensor yang butuh sebanyak ini "
            f"hampir pasti menyelundupkan data pengukuran ke dalam tag.",
            code="storage.too_many_tags",
            details={"count": len(tags), "limit": MAX_EXTRA_TAGS, "context": context},
        )


def estimate_series(
    *, ships: int, sensors_per_ship: int, quality_values: int = 4, source_values: int = 4
) -> int:
    """Perkiraan kasar jumlah seri untuk satu armada.

    Tag identitas bersarang, bukan berkalian: `sensor_id` selalu berada di dalam
    satu `ship_id`, dan `unit` maupun `engine` ditentukan oleh `sensor_id`. Yang
    benar-benar berkalian hanyalah `quality` dan `source`.

    Dipakai untuk memeriksa bahwa anggaran kardinalitas tetap masuk akal saat
    armada bertambah — lihat test-nya.
    """
    return ships * sensors_per_ship * quality_values * source_values
