"""Sunting registry sensor dari Edge Console.

Teknisi yang memasang perangkat di kapal harus bisa mendaftarkan sensor —
memberi nama, menunjuk alamat di LP-A104, menetapkan satuan dan batas wajar —
tanpa membuka terminal maupun menyunting YAML.

Yang **tidak** dilakukan modul ini: menebak arti alamat. Peta alamat LP-A104
memang terdokumentasi di manual (rentang UW/UB, dan formula alamat modul), dan
validasi di sini hanya memakai itu. Tetapi *cara membaca* nilai dari perangkat
belum terkonfirmasi, jadi sensor yang didaftarkan di sini belum tentu langsung
menghasilkan data — lihat docs/hardware/LP-A104.md.

Membiarkan pendaftaran tetap bisa dilakukan itu disengaja: pemetaan sensor adalah
pekerjaan yang bisa diselesaikan lebih dulu, dan menundanya sampai protokolnya
pasti hanya memindahkan pekerjaan ke saat yang paling sibuk.
"""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError as PydanticValidationError

from fleetview_common import ValidationError, get_logger
from fleetview_edge.config.sensors import SensorConfig
from fleetview_edge.protocol.lp_a104.addressing import parse_ub_address, uw_area_of

__all__ = ["delete_sensor", "read_sensors", "upsert_sensor", "validate_channel"]

log = get_logger(__name__)


def validate_channel(channel: str, *, adapter: str) -> str:
    """Periksa bentuk alamat channel terhadap yang terdokumentasi.

    Hanya berlaku untuk adapter `lp_a104`. Adapter simulator dan mock memakai
    penamaan bebas (`sim:me_port_rpm`), dan memaksakan bentuk LP-A104 pada
    keduanya hanya akan menghalangi pengujian.
    """
    channel = channel.strip()
    if not channel:
        raise ValidationError("alamat channel tidak boleh kosong", code="sensor.channel_empty")
    if adapter != "lp_a104":
        return channel

    upper = channel.upper()
    if upper.startswith("UB"):
        parse_ub_address(upper)  # melempar ValidationError bila di luar rentang
        return upper
    if upper.startswith("UW"):
        digits = upper[2:]
        if not digits.isdigit():
            raise ValidationError(
                f"alamat word tidak dikenali: {channel!r}. Bentuknya UW diikuti angka, mis. UW100",
                code="sensor.channel_invalid",
            )
        uw_area_of(int(digits))  # melempar bila di luar rentang terdokumentasi
        return f"UW{int(digits)}"

    raise ValidationError(
        f"alamat {channel!r} tidak dikenali. LP-A104 memakai UW (word, mis. UW100) "
        "atau UB (bit, mis. UB1000)",
        code="sensor.channel_invalid",
        details={"channel": channel},
    )


def read_sensors(path: Path) -> list[dict[str, Any]]:
    """Baca registry apa adanya, sebagai dict — bukan model.

    Console perlu menampilkan juga entri yang tidak valid supaya teknisi bisa
    memperbaikinya. Memuatnya sebagai model akan menolak berkas seluruhnya, dan
    satu entri rusak akan menyembunyikan sensor lain yang baik-baik saja.
    """
    if not path.exists():
        return []
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise ValidationError(
            f"isi {path.name} harus berupa mapping", code="sensor.registry_invalid"
        )
    entries = loaded.get("sensors") or []
    if not isinstance(entries, list):
        raise ValidationError("kunci `sensors` harus berupa daftar", code="sensor.registry_invalid")
    return [e for e in entries if isinstance(e, dict)]


def _write_atomic(path: Path, entries: list[dict[str, Any]]) -> None:
    body = yaml.safe_dump(
        {"sensors": entries}, sort_keys=False, allow_unicode=True, default_flow_style=False
    )
    header = (
        "# Registry sensor kapal ini. Disunting lewat Edge Console.\n"
        "# sensor_id menjadi tag InfluxDB — mengubahnya memutus riwayat data.\n\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(header + body)
            handle.flush()
            os.fsync(handle.fileno())
        temp = Path(tmp)
        temp.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP)
        temp.replace(path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def upsert_sensor(path: Path, entry: dict[str, Any], *, adapter: str = "lp_a104") -> str:
    """Tambah sensor baru atau perbarui yang sudah ada, dikenali dari `sensor_id`.

    Divalidasi dengan model yang sama yang dipakai agent saat memuat registry.
    Menulis dulu lalu berharap valid akan membuat agent gagal start setelah
    restart berikutnya — di kapal, jauh dari orang yang menyuntingnya.
    """
    entry = {k: v for k, v in entry.items() if v not in (None, "")}
    entry["channel"] = validate_channel(str(entry.get("channel", "")), adapter=adapter)

    try:
        model = SensorConfig.model_validate(entry)
    except PydanticValidationError as exc:
        first = exc.errors()[0]
        lokasi = ".".join(str(x) for x in first["loc"]) or "entri"
        raise ValidationError(
            f"{lokasi}: {first['msg']}",
            code="sensor.invalid",
            details={"field": lokasi},
        ) from exc

    entries = read_sensors(path)
    ditulis = model.model_dump(mode="json", exclude_defaults=True)
    # sensor_id, channel, dan metric selalu ditulis walau kebetulan sama dengan
    # default — tanpa ketiganya entri tidak bermakna saat dibaca manusia.
    for wajib in ("sensor_id", "channel", "metric"):
        ditulis[wajib] = getattr(model, wajib)

    # Yang wajib unik adalah pasangan (sensor_id, field_name), BUKAN channel.
    #
    # Channel ganda itu sah dan memang dipakai: satu register perangkat kadang
    # memuat beberapa besaran dengan skala berbeda. Dan GPS memakai beberapa
    # entri ber-sensor_id sama dengan field_name berbeda (lat/lon/sog/cog),
    # supaya koordinatnya menjadi satu titik dan tidak pernah tercatat separuh.
    kunci = (model.sensor_id, model.field_name)
    for i, existing in enumerate(entries):
        if (existing.get("sensor_id"), existing.get("field_name", "value")) == kunci:
            entries[i] = ditulis
            break
    else:
        entries.append(ditulis)

    _write_atomic(path, entries)
    log.info("sensor.disimpan", sensor_id=model.sensor_id, channel=model.channel)
    return model.sensor_id


def delete_sensor(path: Path, sensor_id: str) -> bool:
    """Hapus sensor dari registry.

    Data yang sudah tersimpan TIDAK ikut terhapus — riwayatnya tetap bisa dibaca
    di InfluxDB dan di pusat. Yang berhenti hanyalah pembacaan baru.
    """
    entries = read_sensors(path)
    sisa = [e for e in entries if e.get("sensor_id") != sensor_id]
    if len(sisa) == len(entries):
        return False
    _write_atomic(path, sisa)
    log.info("sensor.dihapus", sensor_id=sensor_id, note="riwayat lama tetap tersimpan")
    return True
