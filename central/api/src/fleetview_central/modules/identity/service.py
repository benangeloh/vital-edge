"""Layanan autentikasi untuk user dan device."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fleetview_central.modules.identity.models import DeviceCredential, Role, User
from fleetview_central.platform.security import TokenService, hash_secret, verify_secret
from fleetview_common import AuthError, get_logger, now_utc

__all__ = ["IdentityService"]

log = get_logger(__name__)


class IdentityService:
    def __init__(self, session: AsyncSession, tokens: TokenService) -> None:
        self._db = session
        self._tokens = tokens

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
        )
        self._db.add(cred)
        await self._db.flush()
        return cred


#: Hash Argon2 atas nilai tetap, dipakai untuk menyamakan waktu verifikasi
#: saat user atau kredensial tidak ditemukan.
_DUMMY_HASH = hash_secret("fleetview-dummy-untuk-menyamakan-waktu")
