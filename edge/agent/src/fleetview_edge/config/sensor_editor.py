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
import re
import stat
import tempfile
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError as PydanticValidationError

from fleetview_common import ValidationError, get_logger
from fleetview_edge.config.sensor_presets import get_preset
from fleetview_edge.config.sensors import SensorConfig
from fleetview_edge.protocol.lp_a104.addressing import parse_ub_address, uw_area_of

__all__ = [
    "add_from_preset",
    "allocate_uw",
    "delete_sensor",
    "read_sensors",
    "upsert_sensor",
    "validate_channel",
]

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
        "# sensor_id menjadi tag InfluxDB, jadi mengubahnya memutus riwayat data.\n\n"
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


#: Kontak pemicu yang dipakai blok salin di ladder.
#:
#: `M000020` dipilih karena rung 26 dan rung 32 pada program panel yang ada
#: sudah memakainya sebagai penanda "inisialisasi selesai" — jadi blok salin
#: ikut berjalan terus selama panel beroperasi, tanpa menambah kontak baru yang
#: harus ikut dipelihara.
LADDER_TRIGGER = "M000020"


def ladder_lines(entries: list[dict[str, Any]]) -> str:
    """Susun blok salin `D -> UW` untuk ditempel ke atLogic.

    Ini jembatan antara dashboard dan panel. Nilai sensor hidup di area `D` milik
    PLC dan **tidak** diekspos ke Ethernet; hanya area `UW` yang terbaca lewat
    Modbus. Jadi tiap sensor menuntut satu baris salin di program panel — satu
    hal yang tidak bisa dibuat dari FleetView, karena ia ada di dalam panel.

    Yang bisa dilakukan: memberi tahu baris yang persis dibutuhkan, sehingga
    teknisi menyalin alih-alih menebak alamat. Salah satu digit pada alamat
    tujuan sudah cukup membuat sensor terbaca nol tanpa gejala lain.
    """
    baris: list[str] = []
    tanpa_sumber: list[str] = []
    for e in sorted(entries, key=lambda x: str(x.get("channel", ""))):
        channel = str(e.get("channel", "")).upper()
        if not channel.startswith("UW"):
            continue
        nama = str(e.get("sensor_id", "?"))
        sumber = str(e.get("panel_source") or "").upper()
        if not sumber:
            tanpa_sumber.append(nama)
            continue
        # UW ditulis dengan padding 5 digit, mengikuti bentuk yang dipakai
        # atLogic pada program panel yang ada (UW00050, bukan UW50).
        tujuan = f"UW{int(channel[2:]):05d}"
        baris.append(f"LOAD  {LADDER_TRIGGER}")
        baris.append(f"MOV   {sumber:<8} {tujuan}      ; {nama}")

    if not baris and not tanpa_sumber:
        return ""
    keluaran = "\n".join(baris)
    if tanpa_sumber:
        catatan = ", ".join(sorted(tanpa_sumber))
        keluaran += f"\n\n; Belum bisa disusun, alamat sumber di panel belum diisi: {catatan}"
    return keluaran


#: Blok UW yang sudah dipastikan bebas dan bisa dibaca lewat Modbus pada panel
#: ini (docs/hardware/konvensi-atlogic.md). Di atas UW81 belum pernah diverifikasi.
UW_FREE_FIRST = 45
UW_FREE_LAST = 80

_D_ADDRESS = re.compile(r"^D(\d{1,5})$")


def allocate_uw(entries: list[dict[str, Any]]) -> str:
    """Alamat UW bebas terendah di blok UW45-UW80.

    Teknisi tidak perlu tahu area UW itu ada: ia memberi tahu alamat D tempat
    nilai berada, dan sistem memilih tujuannya.
    """
    terpakai: set[int] = set()
    for e in entries:
        channel = str(e.get("channel", "")).strip().upper()
        if channel.startswith("UW") and channel[2:].isdigit():
            terpakai.add(int(channel[2:]))
    for nomor in range(UW_FREE_FIRST, UW_FREE_LAST + 1):
        if nomor not in terpakai:
            return f"UW{nomor}"
    raise ValidationError(
        f"blok UW{UW_FREE_FIRST}-UW{UW_FREE_LAST} sudah penuh. Untuk sensor tambahan, uji satu "
        "alamat di atas UW100 lebih dulu dengan angka tetap sebelum dipakai",
        code="sensor.uw_penuh",
    )


def _normalize_d_address(alamat: str) -> str:
    teks = alamat.strip().upper()
    if not teks:
        raise ValidationError(
            "alamat sumber di panel wajib diisi, mis. D00160", code="sensor.alamat_kosong"
        )
    cocok = _D_ADDRESS.match(teks)
    if cocok is None:
        raise ValidationError(
            f"alamat sumber {alamat!r} tidak dikenali. Bentuknya D diikuti angka, mis. D00160",
            code="sensor.alamat_invalid",
            details={"alamat": alamat},
        )
    return f"D{int(cocok.group(1)):05d}"


def add_from_preset(
    path: Path,
    *,
    adapter: str,
    jenis: str,
    sensor_id: str,
    alamat: str = "",
    channel: str = "",
) -> str:
    """Tambah sensor baru dari preset. Menolak nama yang sudah dipakai.

    Berbeda dari `upsert_sensor`, ini tidak pernah menimpa: mengetik nama yang
    sama dua kali di formulir sederhana adalah kesalahan, bukan permintaan ubah.
    Mengubah sensor yang ada dilakukan lewat pengaturan lanjutan.

    Pada LP-A104 ada dua jalan masuk. Dari `alamat` (D…): UW tujuan dipilih
    otomatis dan alamat D dicatat untuk baris ladder. Dari `channel` (hasil
    pencarian): UW itu sudah terbaca, jadi dipakai apa adanya.
    """
    preset = get_preset(jenis)
    sensor_id = sensor_id.strip()
    entries = read_sensors(path)
    if any(e.get("sensor_id") == sensor_id for e in entries):
        raise ValidationError(
            f"nama sensor {sensor_id!r} sudah dipakai", code="sensor.nama_dipakai"
        )

    entry = preset.as_entry()
    entry["sensor_id"] = sensor_id
    channel, alamat = channel.strip(), alamat.strip()
    if adapter == "lp_a104" and not channel:
        entry["panel_source"] = _normalize_d_address(alamat)
        entry["channel"] = allocate_uw(entries)
    else:
        entry["channel"] = channel or alamat
    return upsert_sensor(path, entry, adapter=adapter)
