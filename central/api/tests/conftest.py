"""Fixture integration test terhadap Postgres dan InfluxDB sungguhan.

Test ini **tidak** memakai mock database. Phase 3 dan 4 sudah menunjukkan
kenapa: InfluxDB sungguhan menolak titik di luar retensi bucket, dan mock tidak
pernah memunculkannya. Untuk Postgres, hal yang setara adalah perilaku
`ON CONFLICT`, batasan unik, dan isolasi transaksi — semuanya menentukan jaminan
idempotensi kita, dan tidak satu pun bisa dibuktikan dengan mock.

Jalankan `make up` terlebih dahulu. Tanpa itu test-nya di-skip, bukan gagal.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from fleetview_central.main import create_app
from fleetview_central.platform.db import Base, Database
from fleetview_central.platform.influx import InfluxGateway
from fleetview_central.platform.security import TokenService
from fleetview_central.settings import ApiSettings

POSTGRES_DSN = os.getenv(
    "FLEETVIEW_TEST_POSTGRES_DSN",
    "postgresql+asyncpg://fleetview:fleetview@localhost:5432/fleetview",
)
INFLUX_URL = os.getenv("FLEETVIEW_TEST_INFLUX_URL", "http://localhost:8086")
INFLUX_TOKEN = os.getenv("FLEETVIEW_TEST_INFLUX_TOKEN", "fleetview-dev-token")


async def _services_up() -> bool:
    try:
        db = Database(POSTGRES_DSN)
        async with db.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        await db.dispose()
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"{INFLUX_URL}/health")
        return r.status_code == 200
    except Exception:
        return False


_services_cache: bool | None = None


@pytest_asyncio.fixture
async def services() -> bool:
    """Function-scoped supaya cocok dengan event loop per-test milik
    pytest-asyncio, tetapi hasil probe-nya di-cache — memeriksa dua layanan
    di setiap test akan memperlambat tanpa manfaat."""
    global _services_cache
    if _services_cache is None:
        _services_cache = await _services_up()
    if not _services_cache:
        pytest.skip("Postgres/InfluxDB tidak berjalan; jalankan 'make up'")
    return _services_cache


@pytest_asyncio.fixture
async def database(services: bool) -> AsyncIterator[Database]:
    _ = services
    db = Database(POSTGRES_DSN)
    # Skema dibuat ulang setiap test supaya tidak ada kebocoran state antar test.
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield db
    await db.dispose()


@pytest_asyncio.fixture
async def session(database: Database) -> AsyncIterator[AsyncSession]:
    async with database.session() as s:
        yield s
        await s.commit()


@pytest_asyncio.fixture
async def influx(services: bool) -> AsyncIterator[InfluxGateway]:
    _ = services
    gateway = InfluxGateway(url=INFLUX_URL, org="fleetview", bucket="telemetry", token=INFLUX_TOKEN)
    yield gateway
    await gateway.close()


@pytest.fixture
def tokens() -> TokenService:
    return TokenService("secret-uji-yang-cukup-panjang-untuk-produksi", ttl_seconds=3600)


@pytest_asyncio.fixture
async def client(database: Database, influx: InfluxGateway, tokens: TokenService):
    """Client HTTP async terhadap aplikasi sungguhan.

    Memakai ASGITransport, bukan TestClient sinkron, supaya jalur async —
    connection pool, transaksi — benar-benar dilalui seperti di produksi.
    """
    settings = ApiSettings(
        environment="development",
        log_format="console",
        postgres_dsn=POSTGRES_DSN,
        influx_url=INFLUX_URL,
        influx_token=INFLUX_TOKEN,
    )
    app = create_app(settings)
    app.state.database = database
    app.state.influx = influx
    app.state.tokens = tokens

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def ship_id():
    return uuid4()


@pytest.fixture
def device_id():
    return uuid4()
