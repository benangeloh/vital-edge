"""Autentikasi: hashing kata sandi dan token JWT.

Dua jenis principal yang sangat berbeda:

- **User** — operator yang membuka dashboard. Token berumur pendek, membawa peran.
- **Device** — Edge Agent di kapal. Token **terikat pada satu `ship_id`**, dan
  itu yang mencegah kapal A menyetor data atas nama kapal B, baik karena salah
  konfigurasi maupun karena kredensialnya dicuri.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError

from fleetview_common import AuthError

__all__ = ["DevicePrincipal", "TokenService", "UserPrincipal", "hash_secret", "verify_secret"]

_hasher = PasswordHasher()
ALGORITHM = "HS256"


def hash_secret(secret: str) -> str:
    """Hash kata sandi atau kredensial device dengan Argon2id."""
    return _hasher.hash(secret)


def verify_secret(hashed: str, secret: str) -> bool:
    try:
        return _hasher.verify(hashed, secret)
    except (VerifyMismatchError, VerificationError):
        return False


@dataclass(frozen=True, slots=True)
class UserPrincipal:
    user_id: UUID
    username: str
    roles: frozenset[str]

    def require_role(self, *roles: str) -> None:
        if not roles or self.roles & set(roles):
            return
        raise AuthError(
            f"butuh salah satu peran {sorted(roles)}",
            code="auth.forbidden",
            details={"required": sorted(roles), "have": sorted(self.roles)},
        )


@dataclass(frozen=True, slots=True)
class DevicePrincipal:
    device_id: UUID
    ship_id: UUID
    """Ingest memverifikasi bahwa envelope batch cocok dengan ini. Sebuah device
    tidak pernah bisa menyetor data untuk kapal lain."""


class TokenService:
    def __init__(self, secret: str, *, ttl_seconds: int = 3600) -> None:
        self._secret = secret
        self._ttl = ttl_seconds

    def _encode(self, claims: dict[str, Any], ttl: int | None = None) -> str:
        now = datetime.now(UTC)
        payload = {
            **claims,
            "iat": now,
            "exp": now + timedelta(seconds=ttl if ttl is not None else self._ttl),
        }
        return jwt.encode(payload, self._secret, algorithm=ALGORITHM)

    def issue_user_token(self, user_id: UUID, username: str, roles: set[str]) -> str:
        return self._encode(
            {"sub": str(user_id), "typ": "user", "username": username, "roles": sorted(roles)}
        )

    def issue_device_token(self, device_id: UUID, ship_id: UUID, ttl: int | None = None) -> str:
        return self._encode({"sub": str(device_id), "typ": "device", "ship_id": str(ship_id)}, ttl)

    def decode(self, token: str, *, expect: Literal["user", "device"]) -> dict[str, Any]:
        try:
            claims: dict[str, Any] = jwt.decode(token, self._secret, algorithms=[ALGORITHM])
        except jwt.ExpiredSignatureError as exc:
            raise AuthError("token kedaluwarsa", code="auth.token_expired") from exc
        except jwt.InvalidTokenError as exc:
            raise AuthError("token tidak valid", code="auth.token_invalid") from exc

        # Token device tidak boleh dipakai di endpoint user dan sebaliknya.
        # Tanpa pemeriksaan ini, kredensial device yang bocor dari satu kapal
        # bisa dipakai membaca seluruh armada.
        if claims.get("typ") != expect:
            raise AuthError(
                f"jenis token salah: harapan {expect}, dapat {claims.get('typ')}",
                code="auth.wrong_token_type",
            )
        return claims

    def user_from_token(self, token: str) -> UserPrincipal:
        c = self.decode(token, expect="user")
        return UserPrincipal(
            user_id=UUID(c["sub"]), username=c["username"], roles=frozenset(c.get("roles", []))
        )

    def device_from_token(self, token: str) -> DevicePrincipal:
        c = self.decode(token, expect="device")
        return DevicePrincipal(device_id=UUID(c["sub"]), ship_id=UUID(c["ship_id"]))
