"""Dependency FastAPI: session database dan autentikasi.

Dua jenis principal dipisahkan tegas. Token device tidak pernah bisa memanggil
endpoint user dan sebaliknya — kalau tidak, kredensial device yang bocor dari
satu kapal bisa dipakai membaca seluruh armada.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from fleetview_central.platform.db import Database
from fleetview_central.platform.influx import InfluxGateway
from fleetview_central.platform.security import DevicePrincipal, TokenService, UserPrincipal
from fleetview_common import AuthError

__all__ = [
    "CurrentDevice",
    "CurrentUser",
    "DbSession",
    "Influx",
    "get_db_session",
    "get_influx",
]


def _database(request: Request) -> Database:
    db: Database = request.app.state.database
    return db


def _tokens(request: Request) -> TokenService:
    tokens: TokenService = request.app.state.tokens
    return tokens


def get_influx(request: Request) -> InfluxGateway:
    influx: InfluxGateway = request.app.state.influx
    return influx


async def get_db_session(
    db: Annotated[Database, Depends(_database)],
) -> AsyncIterator[AsyncSession]:
    """Satu transaksi per request.

    Commit di akhir bila tidak ada exception, rollback bila ada. Ingest
    bergantung pada ini: kalau penulisan InfluxDB gagal, transaksi ledger ikut
    di-rollback dan tidak ada ACK yang terbit — central tidak pernah mengklaim
    memiliki data yang sebenarnya tidak tersimpan.
    """
    async with db.session() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        else:
            await session.commit()


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AuthError("header Authorization Bearer tidak ada", code="auth.missing_token")
    return authorization.split(" ", 1)[1].strip()


async def current_user(
    tokens: Annotated[TokenService, Depends(_tokens)],
    authorization: Annotated[str | None, Header()] = None,
) -> UserPrincipal:
    return tokens.user_from_token(_bearer(authorization))


async def current_device(
    tokens: Annotated[TokenService, Depends(_tokens)],
    authorization: Annotated[str | None, Header()] = None,
) -> DevicePrincipal:
    return tokens.device_from_token(_bearer(authorization))


DbSession = Annotated[AsyncSession, Depends(get_db_session)]
Influx = Annotated[InfluxGateway, Depends(get_influx)]
CurrentUser = Annotated[UserPrincipal, Depends(current_user)]
CurrentDevice = Annotated[DevicePrincipal, Depends(current_device)]
