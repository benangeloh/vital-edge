"""Environment Alembic.

Metadata dikumpulkan dengan mengimpor model setiap modul. Modul-modul berbagi
satu `Base` agar migrasi bisa melihat semuanya, tetapi tetap tidak boleh saling
mengimpor model — itu batas yang menjaga monolith tetap modular.
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

# Impor demi efek samping: mendaftarkan tabel ke Base.metadata.
from fleetview_central.modules.fleet import models as _fleet  # noqa: F401
from fleetview_central.modules.identity import models as _identity  # noqa: F401
from fleetview_central.modules.ingest import models as _ingest  # noqa: F401
from fleetview_central.modules.ops import models as _ops  # noqa: F401
from fleetview_central.platform.db import Base

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

dsn = os.getenv("FLEETVIEW_POSTGRES_DSN")
if dsn:
    config.set_main_option("sqlalchemy.url", dsn)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:  # type: ignore[no-untyped-def]
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    engine = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
