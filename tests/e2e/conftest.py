"""Harness end-to-end: tiga kapal simulasi terhadap Central Platform sungguhan.

Bukan simulasi setengah jalan. Setiap kapal memakai **komponen produksi yang
sama** — Collector, MockLPAAdapter, outbox SQLite, StorageWriter, dan Sync
Engine — dan menyetor ke aplikasi FastAPI sungguhan yang menulis ke PostgreSQL
dan InfluxDB sungguhan.

Yang diganti hanya dua hal, dan keduanya disengaja:

- **Adapter perangkat lapangan** memakai `MockLPAAdapter`, karena jalur baca
  LP-A104 masih belum terkonfirmasi (lihat docs/hardware/LP-A104.md).
- **Transport jaringan** memakai `httpx.ASGITransport` alih-alih soket TCP.
  Protokol HTTP-nya sama persis; yang hilang hanya lapisan soket. Sebagai
  gantinya, kegagalan jaringan bisa diskenariokan dengan tepat — putus di
  tengah transfer mustahil diuji dengan andal lewat soket sungguhan.

Jalankan `make up` lebih dulu. Tanpa layanan, test di-skip, bukan gagal.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import Row, text

from fleetview_central.main import create_app
from fleetview_central.modules.fleet.models import Device, Ship
from fleetview_central.modules.identity.service import IdentityService
from fleetview_central.modules.ingest.models import ShipSyncState
from fleetview_central.platform.db import Base, Database
from fleetview_central.platform.influx import InfluxGateway
from fleetview_central.platform.security import TokenService
from fleetview_central.settings import ApiSettings
from fleetview_common import now_micros
from fleetview_contracts import Quality, TelemetryRecord
from fleetview_edge.collector import BackoffPolicy, Collector, CollectorClock
from fleetview_edge.config import SensorConfig, SensorRegistry
from fleetview_edge.outbox import OutboxSink, OutboxStore, SqliteSequenceSource
from fleetview_edge.protocol import MockLPAAdapter, MockScript
from fleetview_edge.storage import InMemoryTelemetryStore, StorageWriter
from fleetview_edge.sync import (
    Batcher,
    FileExportTransport,
    NetworkManager,
    NetworkTransport,
    SyncEngine,
    TransportKind,
    TransportSlot,
)

POSTGRES_DSN = os.getenv(
    "FLEETVIEW_TEST_POSTGRES_DSN",
    "postgresql+asyncpg://fleetview:fleetview@localhost:5432/fleetview",
)
INFLUX_URL = os.getenv("FLEETVIEW_TEST_INFLUX_URL", "http://localhost:8086")
INFLUX_TOKEN = os.getenv("FLEETVIEW_TEST_INFLUX_TOKEN", "fleetview-dev-token")

NO_WAIT = BackoffPolicy(initial_seconds=0.0, max_seconds=0.0, jitter=0.0)

#: Sensor yang dibaca setiap kapal simulasi.
SENSORS = [
    ("me_port_rpm", "rpm", "rpm", 700.0),
    ("me_port_lube_pressure", "pressure", "bar", 4.2),
    ("me_port_exhaust_temp", "temperature", "degC", 410.0),
    ("fuel_level_main", "fuel_level", "percent", 84.0),
]


# --------------------------------------------------------------------- pusat

_services_cache: bool | None = None


async def _services_up() -> bool:
    try:
        db = Database(POSTGRES_DSN)
        async with db.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        await db.dispose()
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(f"{INFLUX_URL}/health")
        return response.status_code == 200
    except Exception:
        return False


@pytest_asyncio.fixture
async def services() -> bool:
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
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield db
    await db.dispose()


@pytest_asyncio.fixture
async def influx(services: bool) -> AsyncIterator[InfluxGateway]:
    _ = services
    gateway = InfluxGateway(url=INFLUX_URL, org="fleetview", bucket="telemetry", token=INFLUX_TOKEN)
    yield gateway
    await gateway.close()


@dataclass
class Central:
    """Central Platform yang berjalan, plus kendali untuk skenario kegagalan."""

    app: object
    database: Database
    influx: InfluxGateway
    tokens: TokenService
    transport: httpx.ASGITransport
    #: Bila True, setiap permintaan ditolak seolah server tidak terjangkau.
    down: bool = False
    #: Byte maksimum yang diterima sebelum sambungan "putus". None = tanpa batas.
    interrupt_after_bytes: int | None = None
    requests: int = 0

    async def session_execute(
        self, sql: str, params: dict[str, object] | None = None
    ) -> Sequence[Row[Any]]:
        async with self.database.engine.connect() as conn:
            return (await conn.execute(text(sql), params or {})).fetchall()


@pytest_asyncio.fixture
async def central(database: Database, influx: InfluxGateway) -> AsyncIterator[Central]:
    settings = ApiSettings(
        environment="development",
        log_format="console",
        postgres_dsn=POSTGRES_DSN,
        influx_url=INFLUX_URL,
        influx_token=INFLUX_TOKEN,
    )
    tokens = TokenService("rahasia-uji-e2e-yang-cukup-panjang", ttl_seconds=3600)
    app = create_app(settings)
    app.state.database = database
    app.state.influx = influx
    app.state.tokens = tokens

    yield Central(
        app=app,
        database=database,
        influx=influx,
        tokens=tokens,
        transport=httpx.ASGITransport(app=app),
    )


# ---------------------------------------------------------------------- kapal


class FlakyTransport(httpx.AsyncBaseTransport):
    """Membungkus transport ASGI supaya kegagalan jaringan bisa diskenariokan.

    Diperlukan karena beberapa skenario mustahil diuji lewat soket sungguhan:
    "putus tepat setelah 200 byte terkirim" tidak bisa diatur dari luar, dan
    menunggu kebetulan menghasilkan test yang kadang lulus kadang tidak.
    """

    def __init__(self, inner: httpx.ASGITransport, central: Central) -> None:
        self._inner = inner
        self._central = central

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self._central.requests += 1
        if self._central.down:
            raise httpx.ConnectError("central tidak terjangkau", request=request)

        limit = self._central.interrupt_after_bytes
        if limit is not None and request.url.path.endswith("/batches"):
            body = request.content
            if len(body) > limit:
                raise httpx.ReadError("sambungan putus di tengah transfer", request=request)

        return await self._inner.handle_async_request(request)


@dataclass
class ShipSim:
    """Satu kapal simulasi lengkap dengan komponen produksinya."""

    slug: str
    ship_id: UUID
    device_id: UUID
    outbox: OutboxStore
    collector: Collector
    adapter: MockLPAAdapter
    storage: StorageWriter
    influx_store: InMemoryTelemetryStore
    sync: SyncEngine
    export_dir: Path
    token: str
    values: dict[str, float] = field(default_factory=dict)

    async def collect(self, rounds: int = 1) -> int:
        """Kumpulkan beberapa putaran. Kembalikan jumlah record baru."""
        before = self.outbox.stats().get("records_pending", 0)
        for _ in range(rounds):
            await self.collector.poll_once()
            # Interval sensor 0 supaya setiap putaran memancarkan data;
            # penyaringan interval sudah diuji terpisah di Phase 2.
            self.collector._next_due_us.clear()
        return self.outbox.stats().get("records_pending", 0) - before

    async def sync_all(self, max_cycles: int = 40) -> int:
        """Setor sampai tidak ada lagi yang tertunda. Kembalikan batch ter-ACK."""
        total = 0
        for _ in range(max_cycles):
            acked = await self.sync.sync_once()
            total += acked
            if acked == 0 and self.pending == 0:
                break
        return total

    @property
    def pending(self) -> int:
        stats = self.outbox.stats()
        return stats.get("records_pending", 0) + stats.get("records_batched", 0)

    @property
    def acked(self) -> int:
        return self.outbox.stats().get("records_acked", 0)

    def drift(self, sensor: str, delta: float) -> None:
        self.values[sensor] = self.values.get(sensor, 0.0) + delta
        self.adapter.set_value(f"ch_{sensor}", self.values[sensor])


@pytest_asyncio.fixture
async def make_ship(
    central: Central, tmp_path: Path
) -> AsyncIterator[Callable[..., Awaitable[ShipSim]]]:
    """Factory: buat kapal terdaftar lengkap dengan Edge Agent-nya."""
    created: list[ShipSim] = []

    async def _make(slug: str, *, script: MockScript | None = None) -> ShipSim:
        ship_id, device_id = uuid4(), uuid4()

        async with central.database.session() as session:
            session.add(Ship(id=ship_id, name=f"KM {slug}", slug=slug))
            await session.flush()
            session.add(Device(id=device_id, ship_id=ship_id, name="Edge Pi"))
            session.add(ShipSyncState(ship_id=ship_id))
            await IdentityService(session, central.tokens).create_device_credential(
                device_id=device_id,
                ship_id=ship_id,
                client_id=f"cred-{slug}",
                secret=f"rahasia-{slug}",
            )
            await session.commit()

        token = central.tokens.issue_device_token(device_id, ship_id)
        client = httpx.AsyncClient(
            transport=FlakyTransport(central.transport, central),
            base_url="http://central.test",
            headers={"Authorization": f"Bearer {token}"},
        )

        values = {name: base for name, _, _, base in SENSORS}
        adapter = MockLPAAdapter(
            {f"ch_{name}": base for name, _, _, base in SENSORS}, script=script
        )
        registry = SensorRegistry(
            [
                SensorConfig(
                    sensor_id=name,
                    channel=f"ch_{name}",
                    metric=metric,
                    unit=unit,
                    poll_interval_seconds=1e-6,
                )
                for name, metric, unit, _ in SENSORS
            ]
        )

        outbox = OutboxStore(":memory:", synchronous_full=False)
        influx_store = InMemoryTelemetryStore()
        storage = StorageWriter(influx_store)
        collector = Collector(
            adapter=adapter,
            registry=registry,
            sink=OutboxSink(outbox, downstream=storage),
            ship_id=ship_id,
            device_id=device_id,
            clock=CollectorClock(SqliteSequenceSource(outbox)),
            poll_interval_seconds=0.01,
            backoff=NO_WAIT,
        )

        export_dir = tmp_path / slug
        export_dir.mkdir(parents=True, exist_ok=True)
        sync = SyncEngine(
            store=outbox,
            batcher=Batcher(
                outbox, max_records=25, agent_version="0.1.0", config_version="cfg-e2e"
            ),
            manager=NetworkManager(
                [
                    TransportSlot(
                        adapter=NetworkTransport(
                            base_url="http://central.test",
                            client=client,
                            kind=TransportKind.LAN,
                        )
                    ),
                    TransportSlot(
                        adapter=FileExportTransport(target_dir=export_dir, ship_slug=slug)
                    ),
                ]
            ),
            backoff=NO_WAIT,
        )

        ship = ShipSim(
            slug=slug,
            ship_id=ship_id,
            device_id=device_id,
            outbox=outbox,
            collector=collector,
            adapter=adapter,
            storage=storage,
            influx_store=influx_store,
            sync=sync,
            export_dir=export_dir,
            token=token,
            values=values,
        )
        created.append(ship)
        return ship

    yield _make

    for ship in created:
        ship.outbox.close()


@pytest_asyncio.fixture
async def fleet(
    make_ship: Callable[..., Awaitable[ShipSim]],
) -> list[ShipSim]:
    """Tiga kapal: SHIP-001, SHIP-002, SHIP-003."""
    return [await make_ship(f"SHIP-{i:03d}") for i in (1, 2, 3)]


@pytest_asyncio.fixture
async def operator(central: Central) -> AsyncIterator[httpx.AsyncClient]:
    """Klien dashboard, dengan token user."""
    async with central.database.session() as session:
        await IdentityService(session, central.tokens).create_user(
            username="operator", password="rahasia-yang-panjang", roles=["admin"]
        )
        await session.commit()
    token = central.tokens.issue_user_token(uuid4(), "operator", {"admin"})
    async with httpx.AsyncClient(
        transport=central.transport,
        base_url="http://central.test",
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        yield client


def make_record(ship: ShipSim, seq: int, *, quality: Quality = Quality.GOOD) -> TelemetryRecord:
    return TelemetryRecord(
        ship_id=ship.ship_id,
        device_id=ship.device_id,
        sensor_id="me_port_rpm",
        sequence_number=seq,
        timestamp=now_micros() - 60_000_000 + seq,
        metric="rpm",
        values={"value": 700.0},
        unit="rpm",
        quality=quality,
    )
