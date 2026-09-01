"""Provisioning perangkat lewat Edge Console.

Tujuannya satu: teknisi yang memasang Raspberry Pi di kapal **tidak perlu
menyentuh terminal maupun menyunting YAML**. Ia hanya memasukkan tiga hal di
halaman web — alamat central, client_id, dan secret — dan sisanya diturunkan.

Yang diturunkan otomatis, bukan diketik ulang:

    ship_id, device_id, ship_name  ← dari jawaban penukaran kredensial

Ini penting bukan sekadar demi kenyamanan. UUID yang diketik ulang dari layar
lain adalah sumber salah ketik yang tidak akan ketahuan sampai data masuk atas
nama kapal yang keliru — dan pada saat itu ia sudah bercampur dengan riwayat
kapal lain.
"""

from __future__ import annotations

import os
import secrets
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import yaml

from fleetview_common import ConfigError, get_logger

__all__ = ["ProvisioningResult", "ShipHandshake", "provision", "verify_credentials"]

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ShipHandshake:
    """Identitas kapal seperti yang diakui central."""

    ship_id: str
    device_id: str
    ship_name: str
    ship_slug: str | None = None


@dataclass(frozen=True, slots=True)
class ProvisioningResult:
    handshake: ShipHandshake
    config_path: Path
    secrets_path: Path


async def verify_credentials(
    *, central_url: str, client_id: str, secret: str, timeout_seconds: float = 15.0
) -> ShipHandshake:
    """Uji kredensial ke central dan ambil identitas kapalnya.

    Dipanggil sebelum apa pun ditulis ke disk. Menulis config lebih dulu lalu
    berharap ia benar akan meninggalkan perangkat setengah terkonfigurasi yang
    gagal diam-diam — persis keadaan yang paling sulit didiagnosis dari darat.
    """
    base = central_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.post(
                f"{base}/api/v1/auth/device/token",
                json={"client_id": client_id, "secret": secret},
            )
    except httpx.HTTPError as exc:
        raise ConfigError(
            f"tidak bisa menjangkau central di {base}: {exc}",
            code="provisioning.unreachable",
            details={"central_url": base},
        ) from exc

    if response.status_code == 401:
        raise ConfigError(
            "central menolak kredensial ini — periksa client_id dan secret, "
            "atau terbitkan yang baru di dashboard",
            code="provisioning.rejected",
            details={"status": 401},
        )
    if response.status_code >= 400:
        raise ConfigError(
            f"central menjawab HTTP {response.status_code}",
            code="provisioning.failed",
            details={"status": response.status_code, "body": response.text[:200]},
        )

    data: dict[str, Any] = response.json().get("data", {})
    missing = [k for k in ("ship_id", "device_id") if not data.get(k)]
    if missing:
        raise ConfigError(
            f"jawaban central tidak memuat {', '.join(missing)} — "
            "kemungkinan versi central terlalu lama",
            code="provisioning.incomplete_response",
            details={"missing": missing},
        )

    return ShipHandshake(
        ship_id=str(data["ship_id"]),
        device_id=str(data["device_id"]),
        # Nama kapal boleh kosong pada central lama; teknisi bisa mengisinya.
        ship_name=str(data.get("ship_name") or "").strip(),
        ship_slug=data.get("ship_slug"),
    )


def _write_atomic(path: Path, content: str, *, mode: int) -> None:
    """Tulis lalu ganti dalam satu langkah.

    Menulis langsung ke berkas tujuan berarti mati listrik di tengah penulisan
    meninggalkan config yang terpotong — dan perangkat tidak bisa start lagi,
    justru di tempat yang paling sulit dijangkau.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temp = Path(tmp)
        temp.chmod(mode)
        # replace() atomik pada sistem berkas yang sama: pembaca tidak akan
        # pernah melihat berkas setengah tertulis.
        temp.replace(path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


async def provision(
    *,
    central_url: str,
    client_id: str,
    secret: str,
    config_path: Path,
    secrets_path: Path,
    ship_name_override: str | None = None,
) -> ProvisioningResult:
    """Uji kredensial, lalu tulis identitas dan rahasianya.

    Urutannya disengaja: verifikasi dulu, tulis belakangan. Kredensial yang salah
    tidak pernah sampai ke disk.
    """
    handshake = await verify_credentials(
        central_url=central_url, client_id=client_id, secret=secret
    )
    name = (ship_name_override or handshake.ship_name or "").strip()
    if not name:
        raise ConfigError(
            "nama kapal tidak diketahui — isi manual di halaman setup",
            code="provisioning.ship_name_missing",
        )

    existing: dict[str, Any] = {}
    if config_path.exists():
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            existing = loaded

    # Hanya bagian identitas dan alamat central yang disentuh. Sisa berkas —
    # pemetaan sensor, retensi, tuning — adalah hasil kerja orang lain dan tidak
    # boleh hilang hanya karena perangkat di-provisioning ulang.
    existing["ship"] = {
        "ship_id": handshake.ship_id,
        "ship_name": name,
        "device_id": handshake.device_id,
    }
    sync = existing.get("sync")
    existing["sync"] = (
        {**sync, "central_url": central_url.rstrip("/")}
        if isinstance(sync, dict)
        else {"central_url": central_url.rstrip("/")}
    )

    _write_atomic(
        config_path,
        "# Sebagian berkas ini ditulis otomatis oleh halaman setup Edge Console.\n"
        "# Bagian `ship` dan `sync.central_url` akan ditimpa bila setup diulang.\n\n"
        + yaml.safe_dump(existing, sort_keys=False, allow_unicode=True),
        mode=stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP,  # 0640
    )

    _write_atomic(
        secrets_path,
        "# Kredensial perangkat. JANGAN disalin ke mana pun.\n"
        f"FLEETVIEW_SYNC__DEVICE_CLIENT_ID={client_id}\n"
        f"FLEETVIEW_SYNC__DEVICE_SECRET={secret}\n",
        mode=stat.S_IRUSR | stat.S_IWUSR,  # 0600 — hanya pemiliknya
    )

    log.info(
        "provisioning.selesai",
        ship_id=handshake.ship_id,
        ship_name=name,
        client_id=client_id,
        # Rahasianya tidak pernah masuk log.
    )
    return ProvisioningResult(handshake, config_path, secrets_path)


def setup_pin(path: Path) -> str:
    """PIN yang melindungi halaman setup, dibuat sekali lalu bertahan.

    Edge Console tidak punya autentikasi, dan selama setup ia harus bisa
    dijangkau dari laptop teknisi di jaringan kapal. Tanpa PIN, siapa pun di
    jaringan itu bisa mengarahkan perangkat ke central lain.

    PIN disimpan di berkas yang hanya bisa dibaca root, dan ditampilkan oleh
    `fleetview-status` — jadi teknisi yang sudah punya akses ke perangkat bisa
    membacanya, sementara yang hanya bisa menjangkau lewat jaringan tidak.
    """
    if path.exists():
        stored = path.read_text(encoding="utf-8").strip()
        if stored:
            return stored
    # Enam digit: cukup untuk menghalangi tebakan lewat jaringan kapal, cukup
    # pendek untuk dibacakan lewat radio.
    pin = f"{secrets.randbelow(1_000_000):06d}"
    _write_atomic(path, pin + "\n", mode=stat.S_IRUSR | stat.S_IWUSR)
    return pin
