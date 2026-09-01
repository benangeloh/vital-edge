"""Siapkan data minimum agar sistem bisa dijalankan di komputer sendiri.

Dijalankan `scripts/dev.sh`, dan **idempoten**: menjalankannya berulang aman,
karena skrip pembungkusnya memang dipanggil setiap kali `make dev`.

Yang disiapkan hanya yang tanpanya sistem tidak bisa jalan sama sekali:
kapal, perangkat, kredensial device, dan satu operator dashboard. Bukan
telemetry contoh — itu dihasilkan sendiri oleh adapter simulator.

UUID-nya tetap dan sama dengan `edge/agent/config/edge.dev.yaml`. Keduanya harus
cocok; kalau tidak, agent akan mengirim atas nama kapal yang tidak terdaftar dan
ditolak central.
"""

from __future__ import annotations

import asyncio
import secrets
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from fleetview_central.modules.fleet.models import Device, Ship
from fleetview_central.modules.identity.models import DeviceCredential, User
from fleetview_central.modules.identity.service import IdentityService
from fleetview_central.modules.ingest.models import ShipSyncState
from fleetview_central.platform.db import Database
from fleetview_central.platform.security import TokenService, hash_secret
from fleetview_central.settings import load_settings

SHIP_ID = UUID("11111111-1111-1111-1111-111111111111")
DEVICE_ID = UUID("22222222-2222-2222-2222-222222222222")
CLIENT_ID = "ship-dev"
USERNAME = "operator"
PASSWORD = "bima2005"
"""Kata sandi operator untuk stack dev lokal. Sengaja pendek dan mudah diingat.

Aman di sini karena central dev hanya mendengar di localhost dan basis datanya
Docker sekali pakai. **Tidak terbawa ke produksi:** `fleetview-admin user add`
menolak kata sandi di bawah 12 karakter, jadi operator di server pusat tidak bisa
dibuat dengan nilai ini bahkan kalau ada yang mencobanya.
"""

ENV_FILE = Path(".devdata/dev.env")
SENSORS_FILE = Path(".devdata/sensors.yaml")
SENSORS_TEMPLATE = Path("edge/agent/config/sensors.example.yaml")


async def main() -> int:
    settings = load_settings()
    db = Database(settings.postgres_dsn)
    tokens = TokenService(settings.jwt_secret or "dev", ttl_seconds=3600)

    async with db.session() as session:
        identity = IdentityService(session, tokens)

        ship = await session.get(Ship, SHIP_ID)
        if ship is None:
            session.add(Ship(id=SHIP_ID, name="KM Simulasi Dev", slug="SHIP-DEV"))
            await session.flush()
            # Device dan ShipSyncState punya foreign key ke Ship, dan urutan
            # insert dalam satu flush tidak dijamin mengikuti urutan add.
            session.add(Device(id=DEVICE_ID, ship_id=SHIP_ID, name="Edge Pi Dev"))
            session.add(ShipSyncState(ship_id=SHIP_ID))
            await session.flush()
            print("  kapal SHIP-DEV didaftarkan")
        else:
            print("  kapal SHIP-DEV sudah ada")

        # Kredensial diterbitkan ulang setiap kali, dan secret-nya disimpan ke
        # berkas. Alasannya: secret hanya ada sebagai hash di basis data, jadi
        # kalau berkasnya hilang tidak ada cara membacanya kembali — menerbitkan
        # ulang lebih sederhana daripada menebak apakah keduanya masih cocok.
        secret = secrets.token_urlsafe(24)
        existing = (
            await session.execute(
                select(DeviceCredential).where(DeviceCredential.client_id == CLIENT_ID)
            )
        ).scalar_one_or_none()
        if existing is not None:
            await session.delete(existing)
            await session.flush()
        await identity.create_device_credential(
            device_id=DEVICE_ID, ship_id=SHIP_ID, client_id=CLIENT_ID, secret=secret
        )
        print("  kredensial device diterbitkan")

        user = (
            await session.execute(select(User).where(User.username == USERNAME))
        ).scalar_one_or_none()
        if user is None:
            await identity.create_user(username=USERNAME, password=PASSWORD, roles=["admin"])
            print(f"  operator {USERNAME} dibuat")
        else:
            # Kata sandi selalu disetel ulang, tidak sekadar dilewati kalau user
            # sudah ada. `scripts/dev.sh` mencetak kata sandi ini di banner; kalau
            # user tersisa dari percobaan sebelumnya dengan kata sandi berbeda,
            # banner itu berbohong dan login gagal tanpa penjelasan apa pun.
            user.password_hash = hash_secret(PASSWORD)
            print(f"  operator {USERNAME} disegarkan")

        await session.commit()

    await db.dispose()

    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    ENV_FILE.write_text(
        "# Dihasilkan scripts/dev_seed.py. Hanya untuk stack dev lokal.\n"
        f"FLEETVIEW_SYNC__DEVICE_CLIENT_ID={CLIENT_ID}\n"
        f"FLEETVIEW_SYNC__DEVICE_SECRET={secret}\n"
    )
    ENV_FILE.chmod(0o600)

    # Disalin, bukan dipakai langsung dari sumbernya. edge.dev.yaml menunjuk ke
    # SENSORS_FILE, dan halaman Sensor di Console MENULIS ke berkas itu lewat
    # upsert_sensor() — kalau agent menunjuk langsung ke berkas contoh yang
    # dilacak git, mencoba fitur kelola-sensor di `make dev` diam-diam menimpa
    # dokumentasi acuan untuk semua orang yang memakai repo ini.
    #
    # Hanya disalin kalau belum ada: dev_seed.py dipanggil setiap `make dev`,
    # dan menimpa ulang akan membuang sensor yang baru saja ditambahkan lewat
    # Console di sesi sebelumnya.
    if not SENSORS_FILE.exists():
        SENSORS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SENSORS_FILE.write_text(SENSORS_TEMPLATE.read_text())
        print(f"  {SENSORS_FILE} disiapkan dari contoh")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
