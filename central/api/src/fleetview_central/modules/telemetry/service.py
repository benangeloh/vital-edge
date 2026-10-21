"""Pengurai hasil query Flux menjadi deret waktu JSON.

CSV beranotasi Flux bukan format yang layak dikirim ke browser: kolomnya
berubah tergantung bentuk query, dan mengurainya di sisi klien berarti setiap
konsumen harus mengulang logika yang sama. Diurai sekali di sini.
"""

from __future__ import annotations

import csv
import io
import re
from typing import Any

__all__ = ["parse_flux_csv", "parse_flux_rows", "safe_sensor_id"]

_SENSOR_ID = re.compile(r"[A-Za-z0-9_.:\-]{1,64}")


def safe_sensor_id(value: str) -> str | None:
    """Kembalikan `value` bila aman dimasukkan ke string Flux, selain itu None.

    Nilainya datang dari query string. String Flux menafsirkan `${...}` dan `"`,
    jadi hanya bentuk id sensor biasa yang boleh lewat.
    """
    return value if _SENSOR_ID.fullmatch(value) else None


def parse_flux_csv(raw: str) -> list[dict[str, Any]]:
    """Ubah CSV beranotasi Flux menjadi daftar seri.

    Flux mengirim satu blok CSV per bentuk tabel, dipisahkan baris kosong, dan
    header bisa berbeda antar blok. Karena itu blok diproses satu per satu, bukan
    diurai sebagai satu tabel.

    Returns:
        Daftar seri, masing-masing `{sensor_id, measurement, field, unit, points}`
        dengan `points` berupa `[[iso_time, value], ...]`.
    """
    series: dict[tuple[str, ...], dict[str, Any]] = {}

    for block in raw.replace("\r\n", "\n").split("\n\n"):
        rows = [r for r in csv.reader(io.StringIO(block)) if r]
        if not rows:
            continue

        header = next((r for r in rows if len(r) > 1 and r[1] == "result"), None)
        if header is None:
            continue
        index = {name: i for i, name in enumerate(header)}
        if "_time" not in index or "_value" not in index:
            continue

        for row in rows:
            if len(row) < 2 or row[1] in ("result", ""):
                continue
            key = (
                _cell(row, index, "sensor_id"),
                _cell(row, index, "_measurement"),
                _cell(row, index, "_field"),
            )
            entry = series.setdefault(
                key,
                {
                    "sensor_id": key[0],
                    "measurement": key[1],
                    "field": key[2],
                    "unit": _cell(row, index, "unit") or None,
                    "points": [],
                },
            )
            value = _cell(row, index, "_value")
            if value == "":
                continue
            try:
                numeric: float | str = float(value)
            except ValueError:
                numeric = value
            entry["points"].append([_cell(row, index, "_time"), numeric])

    return sorted(series.values(), key=lambda s: (s["sensor_id"], s["field"]))


def _cell(row: list[str], index: dict[str, int], name: str) -> str:
    position = index.get(name)
    if position is None or position >= len(row):
        return ""
    return row[position]


def parse_flux_rows(raw: str) -> list[dict[str, Any]]:
    """Ubah CSV beranotasi Flux menjadi baris tabel logger, terbaru dulu.

    Berbeda dari `parse_flux_csv` yang mengelompokkan menjadi seri untuk chart,
    di sini satu record tetap satu baris: itulah yang dibaca teknisi di tabel,
    dan yang diunduh sebagai CSV.
    """
    rows: list[dict[str, Any]] = []

    for block in raw.replace("\r\n", "\n").split("\n\n"):
        records = [r for r in csv.reader(io.StringIO(block)) if r]
        header = next((r for r in records if len(r) > 1 and r[1] == "result"), None)
        if header is None:
            continue
        index = {name: i for i, name in enumerate(header)}
        if "_time" not in index or "_value" not in index:
            continue

        for record in records:
            # Baris data Flux asli berbunyi `,_result,0,...`, tetapi nama result
            # boleh kosong (`,,0,...`) bila query tidak menamainya; keduanya sah.
            if len(record) < 2 or record[1] == "result" or record[0].startswith("#"):
                continue
            value = _cell(record, index, "_value")
            if value == "":
                continue
            try:
                numeric: float | str = float(value)
            except ValueError:
                numeric = value
            rows.append(
                {
                    "time": _cell(record, index, "_time"),
                    "sensor_id": _cell(record, index, "sensor_id"),
                    "metric": _cell(record, index, "_measurement"),
                    "field": _cell(record, index, "_field"),
                    "value": numeric,
                    "unit": _cell(record, index, "unit") or None,
                    "quality": _cell(record, index, "quality") or None,
                }
            )

    rows.sort(key=lambda r: r["time"], reverse=True)
    return rows
