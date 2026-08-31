"""Enkode TelemetryRecord menjadi InfluxDB line protocol.

Ditulis sendiri, bukan memakai `influxdb-client`, karena dua alasan:

1. **Berat di Raspberry Pi.** Client resmi menarik reactivex dan beberapa
   dependency lain untuk sesuatu yang bagi kita hanya dua endpoint HTTP. Agent
   ini berjalan 24/7 di perangkat 4 GB bersama InfluxDB itu sendiri.
2. **Bisa diuji tanpa database.** Enkoder murni berarti seluruh aturan escaping
   dan pemetaan tag bisa diuji tanpa perlu InfluxDB berjalan — dan aturan
   escaping itulah bagian yang paling mudah salah.

Referensi: InfluxDB v2 line protocol.
    measurement[,tag=value...] field=value[,field=value...] [timestamp]
"""

from __future__ import annotations

from fleetview_contracts import TelemetryRecord

__all__ = ["encode_record", "encode_records", "escape_field_string", "escape_tag"]

# Line protocol menuntut escaping yang berbeda di setiap posisi. Salah satu
# alasan enkoder ini ditulis sendiri adalah supaya aturannya kelihatan dan
# bisa diuji, bukan tersembunyi di dalam library.
_MEASUREMENT_ESCAPES = {",": r"\,", " ": r"\ "}
_TAG_ESCAPES = {",": r"\,", "=": r"\=", " ": r"\ "}
_FIELD_KEY_ESCAPES = _TAG_ESCAPES
_FIELD_STRING_ESCAPES = {"\\": "\\\\", '"': '\\"'}


def _escape(value: str, table: dict[str, str]) -> str:
    out = value
    # Backslash harus lebih dulu, kalau tidak escape berikutnya ikut ter-escape.
    if "\\" in table:
        out = out.replace("\\", table["\\"])
    for char, replacement in table.items():
        if char == "\\":
            continue
        out = out.replace(char, replacement)
    return out


def escape_tag(value: str) -> str:
    """Escape kunci tag, nilai tag, dan kunci field."""
    return _escape(value, _TAG_ESCAPES)


def escape_field_string(value: str) -> str:
    """Escape isi field bertipe string. Hasilnya belum termasuk tanda kutip."""
    return _escape(value, _FIELD_STRING_ESCAPES)


def _encode_field_value(value: float | int | bool | str) -> str:
    if isinstance(value, bool):
        # Diperiksa sebelum int, karena bool adalah subclass int di Python.
        # Salah urutan akan menulis `1i` dan tipenya berubah jadi integer.
        return "true" if value else "false"
    if isinstance(value, int):
        # Sufiks `i` menandai integer. Tanpa ini InfluxDB menyimpannya sebagai
        # float, dan penghitung yang besar mulai kehilangan presisi.
        return f"{value}i"
    if isinstance(value, float):
        return repr(value)
    return f'"{escape_field_string(value)}"'


def encode_record(record: TelemetryRecord) -> str:
    """Ubah satu record menjadi satu baris line protocol.

    Timestamp yang dipakai adalah **waktu pengukuran** (`record.timestamp`),
    bukan waktu penulisan. Ini penting: data hasil buffering saat InfluxDB
    sempat mati harus tetap mendarat di posisi waktu yang benar, bukan
    menggumpal di detik saat sambungan pulih.

    Presisi mikrodetik — penulisan harus memakai `precision=us`.
    """
    measurement = _escape(record.metric, _MEASUREMENT_ESCAPES)

    # Tag identitas selalu ada dan selalu urut, sehingga baris yang sama
    # menghasilkan byte yang sama — memudahkan pengujian dan diff.
    tags: dict[str, str] = {
        "ship_id": str(record.ship_id),
        "device_id": str(record.device_id),
        "sensor_id": record.sensor_id,
        "quality": record.quality.value,
        "source": record.source.value,
    }
    if record.unit is not None:
        tags["unit"] = record.unit
    tags.update(record.tags)

    tag_part = ",".join(
        f"{escape_tag(k)}={escape_tag(v)}" for k, v in sorted(tags.items()) if v != ""
    )

    field_part = ",".join(
        f"{escape_tag(k)}={_encode_field_value(v)}" for k, v in sorted(record.values.items())
    )

    prefix = f"{measurement},{tag_part}" if tag_part else measurement
    return f"{prefix} {field_part} {record.timestamp}"


def encode_records(records: list[TelemetryRecord]) -> str:
    """Ubah banyak record menjadi satu payload line protocol."""
    return "\n".join(encode_record(r) for r in records)
