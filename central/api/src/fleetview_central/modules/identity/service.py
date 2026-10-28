"""Layanan autentikasi untuk user dan device."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fleetview_central.modules.identity.models import DeviceCredential, Role, User
from fleetview_central.platform.secretbox import SecretBox
from fleetview_central.platform.security import TokenService, hash_secret, verify_secret
from fleetview_common import AuthError, ValidationError, get_logger, now_utc

__all__ = ["IdentityService"]

log = get_logger(__name__)


class IdentityService:
    def __init__(
        self,
        session: AsyncSession,
        tokens: TokenService,
        secretbox: SecretBox | None = None,
    ) -> None:
        self._db = session
        self._tokens = tokens
        self._secretbox = secretbox

    async def authenticate_user(self, username: str, password: str) -> str:
        user = await self._db.scalar(select(User).where(User.username == username))

        # Verifikasi tetap dijalankan meski user tidak ada, memakai hash dummy.
        # Tanpa itu, selisih waktu respons membocorkan username mana yang
        # terdaftar — cukup untuk menyusun daftar target sebelum menebak sandi.
        stored = user.password_hash if user else _DUMMY_HASH
        ok = verify_secret(stored, password)

        if user is None or not ok or not user.is_active:
            log.warning("auth.login_failed", username=username)
            raise AuthError("username atau kata sandi salah", code="auth.invalid_credentials")

        user.last_login_at = now_utc()
        await self._db.flush()
        return self._tokens.issue_user_token(user.id, user.username, {r.name for r in user.roles})

    async def authenticate_device(self, client_id: str, secret: str) -> tuple[str, UUID, UUID]:
        cred = await self._db.scalar(
            select(DeviceCredential).where(DeviceCredential.client_id == client_id)
        )
        stored = cred.secret_hash if cred else _DUMMY_HASH
        ok = verify_secret(stored, secret)

        if cred is None or not ok or not cred.is_active or cred.revoked_at is not None:
            log.warning("auth.device_login_failed", client_id=client_id)
            raise AuthError("kredensial device ditolak", code="auth.invalid_credentials")

        cred.last_used_at = now_utc()
        await self._db.flush()
        token = self._tokens.issue_device_token(cred.device_id, cred.ship_id)
        return token, cred.device_id, cred.ship_id

    async def create_user(
        self,
        *,
        username: str,
        password: str,
        email: str | None = None,
        roles: list[str] | None = None,
    ) -> User:
        user = User(username=username, email=email, password_hash=hash_secret(password))
        for name in roles or []:
            role = await self._db.scalar(select(Role).where(Role.name == name))
            if role is None:
                role = Role(name=name)
                self._db.add(role)
                await self._db.flush()
            user.roles.append(role)
        self._db.add(user)
        await self._db.flush()
        return user

    async def create_device_credential(
        self, *, device_id: UUID, ship_id: UUID, client_id: str, secret: str
    ) -> DeviceCredential:
        cred = DeviceCredential(
            device_id=device_id,
            ship_id=ship_id,
            client_id=client_id,
            secret_hash=hash_secret(secret),
            # Salinan terenkripsi hanya dibuat bila kuncinya ada. Tanpa itu
            # perilakunya sama seperti sebelum fitur ini: hash saja.
            secret_encrypted=(
                self._secretbox.encrypt(secret)
                if self._secretbox is not None and self._secretbox.enabled
                else None
            ),
        )
        self._db.add(cred)
        await self._db.flush()
        return cred

    async def device_credential(self, client_id: str) -> DeviceCredential | None:
        return (
            await self._db.execute(
                select(DeviceCredential).where(DeviceCredential.client_id == client_id)
            )
        ).scalar_one_or_none()

    async def credentials_for_device(self, device_id: UUID) -> list[DeviceCredential]:
        """Kredensial sebuah perangkat, terbaru lebih dulu.

        Rahasianya tidak ikut — di basis data ia hanya ada sebagai hash, jadi
        memang tidak ada yang bisa dikembalikan.
        """
        rows = await self._db.execute(
            select(DeviceCredential)
            .where(DeviceCredential.device_id == device_id)
            .order_by(DeviceCredential.created_at.desc())
        )
        return list(rows.scalars().all())

    @property
    def can_recover(self) -> bool:
        """Apakah kredensial yang diterbitkan sekarang bisa dibuka lagi nanti."""
        return self._secretbox is not None and self._secretbox.enabled

    async def active_credential(self, device_id: UUID) -> DeviceCredential | None:
        """Kredensial aktif terbaru sebuah perangkat, atau `None`."""
        rows = await self._db.execute(
            select(DeviceCredential)
            .where(
                DeviceCredential.device_id == device_id,
                DeviceCredential.is_active.is_(True),
                DeviceCredential.revoked_at.is_(None),
            )
            .order_by(DeviceCredential.created_at.desc())
            .limit(1)
        )
        return rows.scalar_one_or_none()

    def reveal_secret(self, cred: DeviceCredential) -> str:
        """Rahasia asli sebuah kredensial, untuk skrip pasang.

        Hanya kredensial yang dibuat saat `credential_key` terisi yang punya
        salinan terenkripsi. Sisanya hanya punya hash dan tidak bisa dipulihkan;
        jawabannya dibuat jelas supaya dashboard bisa menawarkan penerbitan ulang.
        """
        if cred.secret_encrypted is None:
            raise ValidationError(
                "rahasia kredensial ini tidak tersimpan (dibuat sebelum fitur ini). "
                "Terbitkan kredensial baru.",
                code="fleet.credential_not_recoverable",
                details={"client_id": cred.client_id},
            )
        if self._secretbox is None:
            raise ValidationError(
                "kunci enkripsi tidak tersedia di server",
                code="fleet.credential_unreadable",
            )
        return self._secretbox.decrypt(cred.secret_encrypted)

    async def revoke_all_for_ship(self, ship_id: UUID) -> list[str]:
        """Cabut semua kredensial aktif sebuah kapal; kembalikan client_id-nya.

        Dipakai saat kapal dinonaktifkan. Barisnya ditandai, tidak dihapus,
        alasannya sama dengan `revoke_device_credential`.
        """
        rows = await self._db.scalars(
            select(DeviceCredential).where(
                DeviceCredential.ship_id == ship_id, DeviceCredential.revoked_at.is_(None)
            )
        )
        revoked: list[str] = []
        now = now_utc()
        for cred in rows:
            cred.is_active = False
            cred.revoked_at = now
            revoked.append(cred.client_id)
        await self._db.flush()
        return revoked

    async def revoke_device_credential(self, client_id: str) -> DeviceCredential | None:
        """Cabut kredensial sebuah perangkat.

        Baris kredensialnya ditandai, bukan dihapus. Audit log dan penelusuran
        insiden merujuk padanya, dan pertanyaan "kredensial mana yang dipakai,
        dan kapan dicabut" hanya bisa dijawab kalau barisnya masih ada.

        Pencabutan berlaku pada penerbitan token berikutnya. Token yang sudah
        terbit tetap berlaku sampai kedaluwarsa — lihat
        docs/operations/08-ship-decommissioning.md untuk kasus ketika jendela itu
        tidak dapat diterima.
        """
        cred = (
            await self._db.execute(
                select(DeviceCredential).where(DeviceCredential.client_id == client_id)
            )
        ).scalar_one_or_none()
        if cred is None:
            return None
        cred.is_active = False
        cred.revoked_at = now_utc()
        await self._db.flush()
        return cred


#: Hash Argon2 atas nilai tetap, dipakai untuk menyamakan waktu verifikasi
#: saat user atau kredensial tidak ditemukan.
_DUMMY_HASH = hash_secret("fleetview-dummy-untuk-menyamakan-waktu")
