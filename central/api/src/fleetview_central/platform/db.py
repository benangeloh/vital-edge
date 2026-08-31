"""Akses PostgreSQL.

Satu engine, satu session factory, satu `Base`. Modul-modul berbagi `Base` agar
metadata-nya menyatu untuk migrasi, tetapi **tidak boleh mengimpor model milik
modul lain** — itu batas yang menjaga monolith tetap modular.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

__all__ = ["Base", "Database", "get_session"]


class Base(DeclarativeBase):
    """Base deklaratif bersama."""


class Database:
    """Memegang engine dan session factory."""

    def __init__(self, dsn: str, *, echo: bool = False, pool_size: int = 10) -> None:
        # pool_pre_ping: koneksi yang mati diam-diam (restart Postgres, timeout
        # firewall) akan terdeteksi saat diambil, bukan saat query gagal di
        # tengah request ingest.
        self._engine: AsyncEngine = create_async_engine(
            dsn, echo=echo, pool_size=pool_size, max_overflow=5, pool_pre_ping=True
        )
        self._factory = async_sessionmaker(self._engine, expire_on_commit=False)

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    def session(self) -> AsyncSession:
        return self._factory()

    async def dispose(self) -> None:
        await self._engine.dispose()


async def get_session(db: Database) -> AsyncIterator[AsyncSession]:
    async with db.session() as session:
        yield session
