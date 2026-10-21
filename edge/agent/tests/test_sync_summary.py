"""Hitungan record yang dipakai ringkasan pengiriman di Edge Console.

Teknisi hanya perlu tahu dua hal: berapa record yang sudah tersimpan di pusat,
dan berapa yang belum. Angka "belum" harus mencakup data yang tertahan setelah
gagal berulang, karena data itu juga belum ada di pusat.
"""

from __future__ import annotations

from uuid import UUID

import pytest

from fleetview_common import now_utc
from fleetview_contracts import Ack, BatchEnvelope, BatchStatus, TelemetryRecord
from fleetview_edge.collector.supervisor import BackoffPolicy
from fleetview_edge.outbox import OutboxStore
from fleetview_edge.sync import (
    Batcher,
    NetworkManager,
    SyncEngine,
    TransportAdapter,
    TransportHealth,
    TransportKind,
    TransportRejectedError,
    TransportResult,
    TransportSlot,
)

SHIP = UUID("11111111-1111-1111-1111-111111111111")
DEVICE = UUID("22222222-2222-2222-2222-222222222222")


def _records(n: int) -> list[TelemetryRecord]:
    return [
        TelemetryRecord(
            ship_id=SHIP,
            device_id=DEVICE,
            sensor_id="me_port_rpm",
            sequence_number=i,
            timestamp=1_756_612_800_000_000 + i * 1_000_000,
            metric="rpm",
            values={"value": float(700 + i)},
            unit="rpm",
        )
        for i in range(1, n + 1)
    ]


class _Transport(TransportAdapter):
    kind = TransportKind.LAN

    def __init__(self, *, reject: bool = False) -> None:
        self._reject = reject

    async def is_available(self) -> bool:
        return True

    async def health(self) -> TransportHealth:
        return TransportHealth(kind=self.kind, available=True)

    async def send(self, envelope: BatchEnvelope, payload: bytes) -> TransportResult:
        if self._reject:
            raise TransportRejectedError("ditolak pusat", details={})
        ack = Ack(
            batch_id=envelope.batch_id,
            status=BatchStatus.COMMITTED,
            last_contiguous_sequence=envelope.sequence_end,
            server_received_at=now_utc(),
            record_count=envelope.record_count,
        )
        return TransportResult(ack=ack, bytes_sent=len(payload))


@pytest.fixture
def store() -> OutboxStore:
    return OutboxStore(":memory:", synchronous_full=False)


def _engine(store: OutboxStore, transport: TransportAdapter) -> SyncEngine:
    return SyncEngine(
        store=store,
        batcher=Batcher(store, max_records=100),
        manager=NetworkManager([TransportSlot(adapter=transport)]),
        backoff=BackoffPolicy(initial_seconds=0.0, max_seconds=0.0, jitter=0.0),
    )


def test_belum_dikirim_dihitung_sebagai_menunggu(store: OutboxStore) -> None:
    store.append(_records(5))

    progress = _engine(store, _Transport()).progress()

    assert progress.pending_records == 5
    assert progress.acked_records == 0
    assert progress.quarantined_records == 0


async def test_yang_sudah_dikonfirmasi_pusat_dihitung_terkirim(store: OutboxStore) -> None:
    store.append(_records(10))
    engine = _engine(store, _Transport())

    await engine.sync_once()

    progress = engine.progress()
    assert progress.acked_records == 10
    assert progress.pending_records == 0
    assert progress.quarantined_records == 0


async def test_yang_ditolak_pusat_dihitung_tertahan_bukan_hilang(store: OutboxStore) -> None:
    store.append(_records(10))
    engine = _engine(store, _Transport(reject=True))

    await engine.sync_once()

    progress = engine.progress()
    assert progress.quarantined_records == 10
    assert progress.acked_records == 0
    assert progress.quarantined_batches == 1


async def test_ketiganya_terpisah_dan_tidak_tumpang_tindih(store: OutboxStore) -> None:
    store.append(_records(4))
    engine = _engine(store, _Transport())
    await engine.sync_once()
    store.append(_records(3)[:0])  # tidak menambah apa-apa
    baru = [r.model_copy(update={"sequence_number": 100 + i}) for i, r in enumerate(_records(2))]
    store.append(baru)

    progress = engine.progress()

    assert (progress.acked_records, progress.pending_records) == (4, 2)
    assert progress.quarantined_records == 0


async def test_snapshot_console_membawa_angka_terkirim_dan_tertahan(store: OutboxStore) -> None:
    from fleetview_edge.console_context import AgentConsoleContext
    from fleetview_edge.settings import EdgeSettings

    store.append(_records(6))
    engine = _engine(store, _Transport())
    await engine.sync_once()
    settings = EdgeSettings(
        ship={
            "ship_id": str(SHIP),
            "ship_name": "KM Uji Coba",
            "device_id": str(DEVICE),
        },
        storage={"acked_grace_days": 3},
    )
    context = AgentConsoleContext(settings=settings, agent_version="0.1.0", sync_engine=engine)

    snapshot = await context.sync()

    assert snapshot.records_acked == 6
    assert snapshot.records_stuck == 0
    assert snapshot.acked_window_days == 3


class TestPerkiraanUkuran:
    """Ukuran dihitung dari data yang tersimpan di outbox (gzip JSON), yang
    sebanding dengan yang dikirim lewat jaringan. Ini perkiraan, bukan hitungan
    byte di kabel."""

    async def test_terkirim_punya_ukuran_dan_belum_terkirim_nol(self, store: OutboxStore) -> None:
        store.append(_records(10))
        engine = _engine(store, _Transport())
        await engine.sync_once()

        progress = engine.progress()

        assert progress.acked_bytes > 0
        assert progress.unsent_bytes == 0
        assert progress.quarantined_bytes == 0

    def test_yang_menunggu_punya_ukuran_dan_terkirim_nol(self, store: OutboxStore) -> None:
        store.append(_records(5))

        progress = _engine(store, _Transport()).progress()

        assert progress.unsent_bytes > 0
        assert progress.acked_bytes == 0

    async def test_yang_tertahan_termasuk_belum_terkirim(self, store: OutboxStore) -> None:
        store.append(_records(10))
        engine = _engine(store, _Transport(reject=True))
        await engine.sync_once()

        progress = engine.progress()

        assert progress.quarantined_bytes > 0
        assert progress.unsent_bytes == progress.quarantined_bytes
        assert progress.acked_bytes == 0

    def test_lebih_banyak_record_berarti_lebih_besar(self) -> None:
        kecil = OutboxStore(":memory:", synchronous_full=False)
        besar = OutboxStore(":memory:", synchronous_full=False)
        kecil.append(_records(5))
        besar.append(_records(200))

        assert (
            _engine(besar, _Transport()).progress().unsent_bytes
            > _engine(kecil, _Transport()).progress().unsent_bytes
        )

    async def test_snapshot_console_membawa_ukuran(self, store: OutboxStore) -> None:
        from fleetview_edge.console_context import AgentConsoleContext
        from fleetview_edge.settings import EdgeSettings

        store.append(_records(6))
        engine = _engine(store, _Transport())
        await engine.sync_once()
        store.append(
            [r.model_copy(update={"sequence_number": 50 + i}) for i, r in enumerate(_records(3))]
        )
        settings = EdgeSettings(
            ship={"ship_id": str(SHIP), "ship_name": "KM Uji Coba", "device_id": str(DEVICE)}
        )
        context = AgentConsoleContext(settings=settings, agent_version="0.1.0", sync_engine=engine)

        snapshot = await context.sync()

        assert snapshot.acked_size_bytes > 0
        assert snapshot.unsent_size_bytes > 0
        assert snapshot.stuck_size_bytes == 0
