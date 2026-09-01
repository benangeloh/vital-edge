"""CLI administrasi armada.

Kenapa CLI dan bukan endpoint HTTP: perintah di sini menerbitkan dan mencabut
kredensial. Menjalankannya di server, lewat shell yang aksesnya sudah dibatasi,
membuat `secret` yang baru diterbitkan tidak pernah melewati reverse proxy, log
akses, atau riwayat peramban. Ia juga tidak menambah permukaan serang pada API
yang menghadap ke 70 kapal.

Perintah-perintah ini dipakai oleh docs/operations/07-ship-onboarding.md dan
docs/operations/08-ship-decommissioning.md.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import secrets
import sys
from uuid import UUID

from sqlalchemy import select

from fleetview_central.modules.fleet.models import Device, Ship
from fleetview_central.modules.identity.models import DeviceCredential, User
from fleetview_central.modules.identity.service import IdentityService
from fleetview_central.platform.db import Database
from fleetview_central.platform.security import TokenService
from fleetview_central.settings import ApiSettings, load_settings
from fleetview_common import ConfigError

__all__ = ["main"]


async def _ship_add(s: ApiSettings, args: argparse.Namespace) -> int:
    db = Database(s.postgres_dsn)
    async with db.session() as session:
        ship = Ship(
            name=args.name,
            slug=args.slug,
            imo_number=args.imo,
            **({"id": UUID(args.id)} if args.id else {}),
        )
        session.add(ship)
        await session.commit()
        print(f"ship_id  {ship.id}")  # noqa: T201
        print(f"slug     {ship.slug}")  # noqa: T201
    await db.dispose()
    return 0


async def _ship_list(s: ApiSettings, args: argparse.Namespace) -> int:
    db = Database(s.postgres_dsn)
    async with db.session() as session:
        rows = (await session.execute(select(Ship).order_by(Ship.slug))).scalars().all()
        for ship in rows:
            state = "aktif " if ship.is_active else "nonaktif"
            print(f"{state}  {ship.slug:12}  {ship.id}  {ship.name}")  # noqa: T201
    await db.dispose()
    return 0


async def _ship_decommission(s: ApiSettings, args: argparse.Namespace) -> int:
    """Tandai kapal tidak aktif. Barisnya TIDAK dihapus.

    Ledger batch dan telemetry merujuk pada kapal ini; menghapusnya akan memutus
    riwayat yang masih ingin dibaca setelah kapal tidak lagi beroperasi.
    """
    db = Database(s.postgres_dsn)
    async with db.session() as session:
        ship = await session.get(Ship, UUID(args.ship_id))
        if ship is None:
            print(f"kapal tidak ditemukan: {args.ship_id}", file=sys.stderr)  # noqa: T201
            return 1
        ship.is_active = False
        await session.commit()
        print(f"{ship.slug} ditandai nonaktif")  # noqa: T201
        print("Kapal keluar dari perhitungan armada dan alerting.")  # noqa: T201
        print("Telemetry-nya TIDAK dihapus.")  # noqa: T201
    await db.dispose()
    return 0


async def _user_add(s: ApiSettings, args: argparse.Namespace) -> int:
    """Buat operator dashboard.

    Ini satu-satunya jalan membuat user pertama: tanpa endpoint pendaftaran
    mandiri, dan itu disengaja — sebuah platform yang mengawasi 70 kapal tidak
    boleh punya jalur pendaftaran terbuka. Konsekuensinya, instalasi baru harus
    melewati langkah ini sebelum dashboard bisa dipakai.
    """
    password = args.password or getpass.getpass("Kata sandi: ")
    if len(password) < 12:
        print("kata sandi minimal 12 karakter", file=sys.stderr)  # noqa: T201
        return 1

    db = Database(s.postgres_dsn)
    async with db.session() as session:
        existing = (
            await session.execute(select(User).where(User.username == args.username))
        ).scalar_one_or_none()
        if existing is not None:
            print(f"user sudah ada: {args.username}", file=sys.stderr)  # noqa: T201
            await db.dispose()
            return 1

        identity = IdentityService(session, TokenService(s.jwt_secret or "cli", ttl_seconds=60))
        await identity.create_user(
            username=args.username, password=password, email=args.email, roles=args.roles
        )
        await session.commit()
    print(f"user {args.username} dibuat, peran: {', '.join(args.roles) or '(tanpa peran)'}")  # noqa: T201
    await db.dispose()
    return 0


async def _user_list(s: ApiSettings, args: argparse.Namespace) -> int:
    db = Database(s.postgres_dsn)
    async with db.session() as session:
        rows = (await session.execute(select(User).order_by(User.username))).scalars().all()
        for user in rows:
            roles = ", ".join(sorted(r.name for r in user.roles)) or "-"
            print(f"  {user.username:20}  {roles}")  # noqa: T201
    await db.dispose()
    return 0


async def _device_add(s: ApiSettings, args: argparse.Namespace) -> int:
    db = Database(s.postgres_dsn)
    async with db.session() as session:
        device = Device(
            ship_id=UUID(args.ship_id),
            name=args.name,
            hardware=args.hardware,
            **({"id": UUID(args.id)} if args.id else {}),
        )
        session.add(device)
        await session.commit()
        print(f"device_id  {device.id}")  # noqa: T201
    await db.dispose()
    return 0


async def _credential_issue(s: ApiSettings, args: argparse.Namespace) -> int:
    db = Database(s.postgres_dsn)
    async with db.session() as session:
        device = await session.get(Device, UUID(args.device_id))
        if device is None:
            print(f"perangkat tidak ditemukan: {args.device_id}", file=sys.stderr)  # noqa: T201
            return 1

        # 32 byte dari token_urlsafe, bukan kata sandi yang dipilih manusia:
        # kredensial ini tidak pernah diketik, hanya disalin ke secrets.env.
        secret = secrets.token_urlsafe(32)
        identity = IdentityService(session, TokenService(s.jwt_secret or "cli", ttl_seconds=60))
        await identity.create_device_credential(
            device_id=device.id,
            ship_id=device.ship_id,
            client_id=args.client_id,
            secret=secret,
        )
        await session.commit()

    print(f"client_id  {args.client_id}")  # noqa: T201
    print(f"secret     {secret}")  # noqa: T201
    print()  # noqa: T201
    # Peringatan ini bukan basa-basi: di basis data hanya ada hash Argon2id,
    # jadi tidak ada cara membacanya kembali nanti.
    print("Secret hanya ditampilkan SEKALI. Salin sekarang ke", file=sys.stderr)  # noqa: T201
    print("/etc/fleetview/secrets.env di Pi kapal.", file=sys.stderr)  # noqa: T201
    print("Kalau hilang: terbitkan yang baru, lalu cabut yang lama.", file=sys.stderr)  # noqa: T201
    await db.dispose()
    return 0


async def _credential_revoke(s: ApiSettings, args: argparse.Namespace) -> int:
    db = Database(s.postgres_dsn)
    async with db.session() as session:
        identity = IdentityService(session, TokenService(s.jwt_secret or "cli", ttl_seconds=60))
        cred = await identity.revoke_device_credential(args.client_id)
        if cred is None:
            print(f"kredensial tidak ditemukan: {args.client_id}", file=sys.stderr)  # noqa: T201
            return 1
        await session.commit()
    print(f"{args.client_id} dicabut")  # noqa: T201
    print(  # noqa: T201
        "Berlaku pada penerbitan token berikutnya. Token yang sudah terbit tetap\n"
        "berlaku sampai kedaluwarsa (default 1 jam)."
    )
    await db.dispose()
    return 0


async def _credential_list(s: ApiSettings, args: argparse.Namespace) -> int:
    db = Database(s.postgres_dsn)
    async with db.session() as session:
        rows = (
            (await session.execute(select(DeviceCredential).order_by(DeviceCredential.client_id)))
            .scalars()
            .all()
        )
        for cred in rows:
            state = "aktif " if cred.is_active and cred.revoked_at is None else "dicabut"
            print(f"{state}  {cred.client_id:16}  ship={cred.ship_id}")  # noqa: T201
    await db.dispose()
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fleetview-admin",
        description="Administrasi armada FleetView",
    )
    sub = parser.add_subparsers(dest="group", required=True)

    ship = sub.add_parser("ship", help="kelola kapal").add_subparsers(dest="cmd", required=True)
    p = ship.add_parser("add", help="daftarkan kapal baru")
    p.add_argument("--name", required=True)
    p.add_argument("--slug", required=True, help="mis. SHIP-071; dipakai nama folder export USB")
    p.add_argument("--imo", default=None)
    # --id dipakai saat UUID-nya sudah ditentukan di tempat lain: config kapal
    # simulasi, atau pendaftaran ulang setelah pemulihan bencana, ketika kapal
    # harus memakai ship_id lamanya supaya telemetry lama tetap tersambung.
    p.add_argument("--id", default=None, help="pakai UUID tertentu, bukan acak")
    p.set_defaults(fn=_ship_add)
    ship.add_parser("list", help="daftar kapal").set_defaults(fn=_ship_list)
    p = ship.add_parser("decommission", help="tandai kapal nonaktif")
    p.add_argument("ship_id")
    p.set_defaults(fn=_ship_decommission)

    device = sub.add_parser("device", help="kelola perangkat").add_subparsers(
        dest="cmd", required=True
    )
    p = device.add_parser("add", help="daftarkan perangkat")
    p.add_argument("--ship-id", required=True, dest="ship_id")
    p.add_argument("--name", required=True)
    p.add_argument("--hardware", default=None)
    p.add_argument("--id", default=None, help="pakai UUID tertentu, bukan acak")
    p.set_defaults(fn=_device_add)

    user = sub.add_parser("user", help="kelola operator dashboard").add_subparsers(
        dest="cmd", required=True
    )
    p = user.add_parser("add", help="buat operator dashboard")
    p.add_argument("--username", required=True)
    p.add_argument(
        "--password",
        default=None,
        help="kosongkan agar diminta interaktif — supaya tidak tersimpan di riwayat shell",
    )
    p.add_argument("--email", default=None)
    p.add_argument("--roles", nargs="*", default=["admin"], help="default: admin")
    p.set_defaults(fn=_user_add)
    user.add_parser("list", help="daftar operator").set_defaults(fn=_user_list)

    cred = sub.add_parser("credential", help="kelola kredensial device").add_subparsers(
        dest="cmd", required=True
    )
    p = cred.add_parser("issue", help="terbitkan kredensial baru")
    p.add_argument("--device-id", required=True, dest="device_id")
    p.add_argument("--client-id", required=True, dest="client_id")
    p.set_defaults(fn=_credential_issue)
    p = cred.add_parser("revoke", help="cabut kredensial")
    p.add_argument("client_id")
    p.set_defaults(fn=_credential_revoke)
    cred.add_parser("list", help="daftar kredensial").set_defaults(fn=_credential_list)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"config error [{exc.code}]: {exc.message}", file=sys.stderr)  # noqa: T201
        return 2
    result: int = asyncio.run(args.fn(settings, args))
    return result


if __name__ == "__main__":
    raise SystemExit(main())
