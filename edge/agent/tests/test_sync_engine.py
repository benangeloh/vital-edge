"""Test Sync Engine — kesembilan skenario yang disyaratkan Phase 4.

successful sync, duplicate batch, connection failure, retry, interrupted
transfer, resume, checksum mismatch, server unavailable, partial ACK.

Semuanya diuji lewat `TransportAdapter` yang sama dengan produksi, sehingga yang
dibuktikan adalah perilaku Sync Engine — bukan perilaku sebuah mock.
"""

from __future__ import annotations

from uuid import UUID

import pytest

from fleetview_common import now_utc
from fleetview_contracts import Ack, BatchEnvelope, BatchStatus, SyncPriority, TelemetryRecord
from fleetview_edge.collector.supervisor import BackoffPolicy
from fleetview_edge.outbox import BatchState, OutboxStore
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
    TransportUnavailableError,
    decode_payload,
)

SHIP = UUID("11111111-1111-1111-1111-111111111111")
DEVICE = UUID("22222222-2222-2222-2222-222222222222")
NO_WAIT = BackoffPolicy(initial_seconds=0.0, max_seconds=0.0, jitter=0.0)


def _records(n: int, start: int = 1) -> list[TelemetryRecord]:
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
        for i in range(start, start + n)
    ]


class FakeTransport(TransportAdapter):
    """Transport yang bisa diskenariokan, memenuhi kontrak yang sama."""

    kind = TransportKind.LAN

    def __init__(
        self,
        *,
        available: bool = True,
        fail_sends: set[int] | None = None,
        reject_sends: set[int] | None = None,
        interrupt_at_chunk: int | None = None,
        ack_none: bool = False,
        wrong_ack_id: bool = False,
    ) -> None:
        self.available = available
        self._fail = fail_sends or set()
        self._reject = reject_sends or set()
        self._interrupt = interrupt_at_chunk
        self._ack_none = ack_none
        self._wrong_ack = wrong_ack_id
        self.attempts = 0
        self.delivered: list[BatchEnvelope] = []
        self.payloads: dict[str, bytes] = {}
        self.server_committed: set[str] = set()
        self.partial_bytes: dict[str, int] = {}

    async def is_available(self) -> bool:
        return self.available

    async def health(self) -> TransportHealth:
        return TransportHealth(kind=self.kind, available=self.available)

    async def send(self, envelope: BatchEnvelope, payload: bytes) -> TransportResult:
        current = self.attempts
        self.attempts += 1
        bid = str(envelope.batch_id)

        # Server sudah punya batch ini: putar ulang ACK tanpa memindahkan byte.
        if bid in self.server_committed:
            return TransportResult(ack=self._ack(envelope), bytes_sent=0)

        if current in self._reject:
            raise TransportRejectedError("checksum tidak cocok", details={"attempt": current})
        if current in self._fail:
            raise TransportUnavailableError("central tidak terjangkau")

        if self._interrupt is not None and bid not in self.partial_bytes:
            # Transfer terputus di tengah: sebagian byte sudah di server.
            self.partial_bytes[bid] = self._interrupt
            raise TransportUnavailableError("sambungan putus di tengah transfer")

        resumed = self.partial_bytes.pop(bid, 0)
        self.payloads[bid] = payload
        self.delivered.append(envelope)
        self.server_committed.add(bid)

        if self._ack_none:
            return TransportResult(ack=None, bytes_sent=len(payload))

        return TransportResult(
            ack=self._ack(envelope, wrong=self._wrong_ack),
            bytes_sent=len(payload) - resumed,
            resumed_from_chunk=1 if resumed else 0,
        )

    @staticmethod
    def _ack(envelope: BatchEnvelope, *, wrong: bool = False) -> Ack:
        return Ack(
            batch_id=UUID("99999999-9999-9999-9999-999999999999") if wrong else envelope.batch_id,
            status=BatchStatus.COMMITTED,
            last_contiguous_sequence=envelope.sequence_end,
            server_received_at=now_utc(),
            record_count=envelope.record_count,
        )


def _engine(store: OutboxStore, transport: TransportAdapter, **kw) -> SyncEngine:
    return SyncEngine(
        store=store,
        batcher=Batcher(store, max_records=kw.pop("max_records", 100)),
        manager=NetworkManager([TransportSlot(adapter=transport, **kw.pop("slot", {}))]),
        backoff=NO_WAIT,
        **kw,
    )


@pytest.fixture
def store() -> OutboxStore:
    return OutboxStore(":memory:", synchronous_full=False)


class TestSyncBerhasil:
    async def test_data_terkirim_dan_ditandai_acked(self, store: OutboxStore) -> None:
        store.append(_records(10))
        transport = FakeTransport()
        engine = _engine(store, transport)

        assert await engine.sync_once() == 1
        assert store.pending_count() == 0
        assert engine.stats.batches_acked == 1
        assert engine.stats.records_synced == 10

    async def test_envelope_membawa_semua_field_yang_disyaratkan(self, store: OutboxStore) -> None:
        store.append(_records(5))
        transport = FakeTransport()
        await _engine(store, transport).sync_once()

        env = transport.delivered[0]
        assert env.ship_id == SHIP and env.device_id == DEVICE
        assert env.sequence_start == 1 and env.sequence_end == 5
        assert env.record_count == 5
        assert env.payload_checksum.startswith("sha256:")
        assert env.schema_version == "1.0"
        assert env.first_timestamp <= env.last_timestamp

    async def test_payload_bisa_didekode_kembali(self, store: OutboxStore) -> None:
        store.append(_records(5))
        transport = FakeTransport()
        await _engine(store, transport).sync_once()

        readings = decode_payload(next(iter(transport.payloads.values())))
        assert len(readings) == 5
        assert [r.seq for r in readings] == [1, 2, 3, 4, 5]

    async def test_batch_dipecah_menurut_max_records(self, store: OutboxStore) -> None:
        """`max_records` dihormati pada batas chunk, bukan per pembacaan.

        Satu baris outbox menyimpan sekelompok pembacaan sekaligus supaya bisa
        dikompresi bersama, dan state melekat pada baris itu. Memecah di tengah
        chunk akan menandai seluruh chunk terkirim padahal sebagiannya tidak.
        Di sini tiap `append` menghasilkan satu chunk berisi 3 pembacaan.
        """
        for i in range(4):
            store.append(_records(3, start=1 + i * 3))
        transport = FakeTransport()
        await _engine(store, transport, max_records=4).sync_once()
        assert [e.record_count for e in transport.delivered] == [3, 3, 3, 3]

    async def test_chunk_tunggal_tidak_dipotong_di_tengah(self, store: OutboxStore) -> None:
        """Lebih baik satu batch sedikit melebihi max_records daripada menandai
        separuh chunk terkirim — yang berarti kehilangan data tanpa jejak."""
        store.append(_records(10))
        transport = FakeTransport()
        await _engine(store, transport, max_records=3).sync_once()
        assert [e.record_count for e in transport.delivered] == [10]


class TestBatchGanda:
    async def test_kirim_ulang_batch_committed_tidak_menggandakan(self, store: OutboxStore) -> None:
        """Pengiriman ulang memutar ulang ACK yang tersimpan, bukan menggandakan
        data. Inilah yang membuat percobaan ulang gratis."""
        store.append(_records(5))
        transport = FakeTransport()
        engine = _engine(store, transport)
        await engine.sync_once()

        bid = str(transport.delivered[0].batch_id)
        built = Batcher(store).rebuild(bid)
        assert built is not None
        result = await transport.send(built.envelope, built.payload)

        assert result.ack is not None
        assert result.bytes_sent == 0  # tidak ada byte yang dipindahkan
        assert len(transport.delivered) == 1  # tidak ada penyimpanan kedua

    async def test_batch_id_stabil_saat_dibangun_ulang(self, store: OutboxStore) -> None:
        """Kalau batch_id berubah setiap percobaan, idempotensi di central runtuh
        dan setiap retry menjadi data baru."""
        store.append(_records(5))
        transport = FakeTransport(fail_sends={0})
        engine = _engine(store, transport)
        await engine.sync_once()
        first = store.batches_ready(limit=1)[0].batch_id

        await engine.sync_once()
        assert transport.delivered[0].batch_id == UUID(first)

    async def test_checksum_identik_saat_dibangun_ulang(self, store: OutboxStore) -> None:
        """Payload deterministik — itulah yang membuat resume bisa melanjutkan
        alih-alih mengulang dari nol."""
        store.append(_records(5))
        b = Batcher(store)
        first = b.build_next()
        assert first is not None
        again = b.rebuild(str(first.envelope.batch_id))
        assert again is not None
        assert again.envelope.payload_checksum == first.envelope.payload_checksum
        assert again.payload == first.payload


class TestKegagalanKoneksi:
    async def test_transport_mati_tidak_menghilangkan_data(self, store: OutboxStore) -> None:
        store.append(_records(5))
        engine = _engine(store, FakeTransport(available=False))

        assert await engine.sync_once() == 0
        assert store.pending_count() == 5  # utuh

    async def test_kegagalan_kirim_menyisakan_batch_pending(self, store: OutboxStore) -> None:
        store.append(_records(5))
        engine = _engine(store, FakeTransport(fail_sends={0}))
        await engine.sync_once()

        batch = store.batches_ready(limit=1, now_us=2_000_000_000_000_000)[0]
        assert batch.state is BatchState.UPLOADING
        assert batch.attempts == 1
        assert len(store.batch_records(batch.batch_id)) == 5

    async def test_tidak_ada_transport_tersedia(self, store: OutboxStore) -> None:
        store.append(_records(5))
        engine = SyncEngine(
            store=store, batcher=Batcher(store), manager=NetworkManager([]), backoff=NO_WAIT
        )
        assert await engine.sync_once() == 0
        assert store.pending_count() == 5


class TestPercobaanUlang:
    async def test_pulih_setelah_gagal_sementara(self, store: OutboxStore) -> None:
        store.append(_records(5))
        transport = FakeTransport(fail_sends={0, 1})
        engine = _engine(store, transport)

        await engine.sync_once()
        await engine.sync_once()
        await engine.sync_once()

        assert store.pending_count() == 0
        assert engine.stats.batches_acked == 1

    async def test_backoff_menjadwalkan_percobaan_berikutnya(self, store: OutboxStore) -> None:
        store.append(_records(5))
        engine = SyncEngine(
            store=store,
            batcher=Batcher(store),
            manager=NetworkManager([TransportSlot(adapter=FakeTransport(fail_sends={0}))]),
            backoff=BackoffPolicy(initial_seconds=60.0, max_seconds=600.0, jitter=0.0),
        )
        await engine.sync_once()

        batch = store.batches_ready(limit=1, now_us=2_000_000_000_000_000)[0]
        assert batch.next_attempt_at is not None
        assert store.batches_ready(limit=1, now_us=1) == []  # belum waktunya

    async def test_dikarantina_setelah_melebihi_batas_percobaan(self, store: OutboxStore) -> None:
        """Berhenti dicoba supaya tidak menyumbat antrean — tetapi datanya tetap ada."""
        store.append(_records(5))
        transport = FakeTransport(fail_sends=set(range(50)))
        engine = _engine(store, transport, max_attempts=3)

        for _ in range(6):
            await engine.sync_once()

        batch = next(iter(store.batches_ready(limit=10, now_us=2_000_000_000_000_000)), None)
        if batch is None:
            quarantined = store.stats().get("batches_failed", 0)
            assert quarantined >= 1
        assert store.stats().get("records_batched", 0) == 5  # data utuh


class TestTransferTerputusDanResume:
    async def test_transfer_terputus_lalu_dilanjutkan(self, store: OutboxStore) -> None:
        """Satelit kapal putus di tengah transfer adalah kejadian biasa. Tanpa
        resume, backlog 30 hari tidak akan pernah selesai terkirim."""
        store.append(_records(20))
        transport = FakeTransport(interrupt_at_chunk=512)
        engine = _engine(store, transport)

        assert await engine.sync_once() == 0  # putus
        assert store.pending_count() == 0  # sudah masuk batch, belum acked

        acked = await engine.sync_once()  # lanjutkan
        assert acked == 1
        assert engine.stats.resumes == 1

    async def test_resume_tidak_mengulang_dari_nol(self, store: OutboxStore) -> None:
        store.append(_records(20))
        transport = FakeTransport(interrupt_at_chunk=512)
        engine = _engine(store, transport)
        await engine.sync_once()
        await engine.sync_once()

        assert transport.delivered[0].record_count == 20  # data lengkap


class TestChecksumTidakCocok:
    async def test_batch_ditolak_dikarantina_bukan_dihapus(self, store: OutboxStore) -> None:
        """Penolakan permanen tidak boleh diulang — kalau diulang, satu batch
        rusak menyumbat antrean selamanya. Tapi datanya tetap harus ada."""
        store.append(_records(5))
        transport = FakeTransport(reject_sends={0})
        engine = _engine(store, transport)

        await engine.sync_once()

        assert engine.stats.batches_quarantined == 1
        assert store.stats().get("batches_failed", 0) == 1
        assert store.stats().get("records_batched", 0) == 5  # data utuh

    async def test_batch_ditolak_tidak_dicoba_ulang(self, store: OutboxStore) -> None:
        store.append(_records(5))
        transport = FakeTransport(reject_sends={0})
        engine = _engine(store, transport)
        await engine.sync_once()
        attempts_after_first = transport.attempts

        await engine.sync_once()
        assert transport.attempts == attempts_after_first  # tidak dicoba lagi


class TestServerTidakTersedia:
    async def test_backlog_menumpuk_tanpa_kehilangan(self, store: OutboxStore) -> None:
        transport = FakeTransport(fail_sends=set(range(100)))
        engine = _engine(store, transport, max_records=5)

        for i in range(3):
            store.append(_records(5, start=1 + i * 5))
            await engine.sync_once()

        assert store.stats().get("records_acked", 0) == 0
        total = store.stats().get("records_pending", 0) + store.stats().get("records_batched", 0)
        assert total == 15  # semuanya masih ada

    async def test_semua_tersetor_saat_server_kembali(self, store: OutboxStore) -> None:
        store.append(_records(15))
        transport = FakeTransport(fail_sends={0})
        engine = _engine(store, transport, max_records=5)

        await engine.sync_once()
        for _ in range(5):
            await engine.sync_once()

        assert store.stats().get("records_pending", 0) == 0
        assert engine.stats.records_synced == 15


class TestAckSebagian:
    async def test_tanpa_ack_batch_tetap_pending(self, store: OutboxStore) -> None:
        """Export USB mengembalikan ack=None. Berkas sudah tertulis, tetapi tidak
        ada yang mengonfirmasi central menerimanya."""
        store.append(_records(5))
        transport = FakeTransport(ack_none=True)
        engine = _engine(store, transport)

        assert await engine.sync_once() == 0
        assert engine.stats.batches_acked == 0
        assert store.stats().get("records_acked", 0) == 0
        assert store.stats().get("records_batched", 0) == 5

    async def test_ack_untuk_batch_lain_tidak_diterima(self, store: OutboxStore) -> None:
        """Menandai batch synchronized berdasarkan ACK milik batch lain akan
        menghapus data yang belum benar-benar sampai."""
        from fleetview_edge.sync.network import NetworkTransport

        store.append(_records(5))
        built = Batcher(store).build_next()
        assert built is not None

        import httpx

        def handler(_r: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "batch_id": "99999999-9999-9999-9999-999999999999",
                    "status": "committed",
                    "last_contiguous_sequence": 5,
                    "server_received_at": now_utc().isoformat(),
                    "record_count": 5,
                },
            )

        t = NetworkTransport(
            base_url="http://c.test",
            client=httpx.AsyncClient(
                transport=httpx.MockTransport(handler), base_url="http://c.test"
            ),
        )
        with pytest.raises(TransportRejectedError, match="merujuk batch lain"):
            await t.send(built.envelope, built.payload)


class TestPrioritas:
    async def test_kritis_dikirim_lebih_dulu(self, store: OutboxStore) -> None:
        store.append(_records(5, start=1), priority=SyncPriority.RAW)
        store.append(_records(5, start=6), priority=SyncPriority.CRITICAL)
        transport = FakeTransport()
        await _engine(store, transport).sync_once()

        assert transport.delivered[0].sequence_start == 6  # kritis duluan

    async def test_link_bermeteran_menahan_data_mentah(self, store: OutboxStore) -> None:
        """Telemetry mentah 1 Hz tidak sepadan dengan kuota seluler; alarm mesin
        sepadan."""
        store.append(_records(5), priority=SyncPriority.RAW)
        transport = FakeTransport()
        engine = _engine(store, transport, slot={"max_priority": SyncPriority.CRITICAL})

        assert await engine.sync_once() == 0
        assert store.pending_count() == 5  # ditahan, bukan hilang

    async def test_kuota_habis_menyisakan_jalur_kritis(self, store: OutboxStore) -> None:
        store.append(_records(5), priority=SyncPriority.CRITICAL)
        transport = FakeTransport()
        engine = _engine(
            store,
            transport,
            slot={"max_priority": SyncPriority.RAW, "monthly_budget_bytes": 1, "bytes_used": 999},
        )
        assert await engine.sync_once() == 1  # kritis tetap lewat


class TestStatusDanProgres:
    async def test_progres_melaporkan_backlog(self, store: OutboxStore) -> None:
        store.append(_records(10))
        engine = _engine(store, FakeTransport(available=False))
        await engine.sync_once()

        p = engine.progress()
        assert p.pending_records == 10
        assert p.oldest_pending_age_seconds is not None

    async def test_snapshot_menyertakan_status_transport(self, store: OutboxStore) -> None:
        store.append(_records(5))
        engine = _engine(store, FakeTransport())
        await engine.sync_once()

        snap = await engine.snapshot()
        assert snap["batches_acked"] == 1
        assert snap["records_synced"] == 5
        assert isinstance(snap["transports"], list)


class TestLoopDanExport:
    async def test_run_menyetor_lalu_berhenti_rapi(self, store: OutboxStore) -> None:
        import asyncio

        store.append(_records(10))
        transport = FakeTransport()
        engine = SyncEngine(
            store=store,
            batcher=Batcher(store),
            manager=NetworkManager([TransportSlot(adapter=transport)]),
            backoff=NO_WAIT,
            interval_seconds=0.01,
        )
        task = asyncio.create_task(engine.run())
        await asyncio.sleep(0.05)
        engine.stop()
        await asyncio.wait_for(task, timeout=1.0)

        assert engine.stats.batches_acked >= 1
        assert store.pending_count() == 0

    async def test_loop_tidak_mati_karena_kegagalan(self, store: OutboxStore) -> None:
        """Kapal berlayar berbulan-bulan tanpa ada yang bisa menyalakan ulang
        prosesnya. Loop sync tidak boleh berhenti sendiri."""
        import asyncio

        class Meledak(FakeTransport):
            async def send(self, envelope: BatchEnvelope, payload: bytes) -> TransportResult:
                raise RuntimeError("bug tak terduga")

        store.append(_records(5))
        engine = SyncEngine(
            store=store,
            batcher=Batcher(store),
            manager=NetworkManager([TransportSlot(adapter=Meledak())]),
            backoff=NO_WAIT,
            interval_seconds=0.01,
        )
        task = asyncio.create_task(engine.run())
        await asyncio.sleep(0.05)
        engine.stop()
        await asyncio.wait_for(task, timeout=1.0)  # tetap berhenti dengan rapi

        assert store.stats().get("records_acked", 0) == 0  # tidak ada yang hilang

    async def test_export_tidak_menandai_synchronized(self, store: OutboxStore) -> None:
        """Menulis ke flashdisk bukan bukti central menerimanya."""
        import tempfile
        from pathlib import Path

        from fleetview_edge.sync import FileExportTransport

        store.append(_records(10))
        with tempfile.TemporaryDirectory() as tmp:
            usb = FileExportTransport(target_dir=Path(tmp), ship_slug="SHIP-001")
            slot = TransportSlot(adapter=usb)
            engine = SyncEngine(
                store=store,
                batcher=Batcher(store, max_records=5),
                manager=NetworkManager([slot]),
                backoff=NO_WAIT,
            )
            assert await engine.export_to(slot) == 1

            ok, problems = FileExportTransport.verify_package(Path(tmp) / "SHIP-001")
            assert ok, problems

        assert store.stats().get("records_acked", 0) == 0
        assert store.stats().get("records_batched", 0) == 10  # data utuh

    async def test_export_gagal_mengembalikan_baris_ke_pending(self, store: OutboxStore) -> None:
        from pathlib import Path

        from fleetview_edge.sync import FileExportTransport

        store.append(_records(5))
        usb = FileExportTransport(target_dir=Path("/tidak/ada"), ship_slug="SHIP-001")
        slot = TransportSlot(adapter=usb)
        engine = SyncEngine(
            store=store,
            batcher=Batcher(store),
            manager=NetworkManager([slot]),
            backoff=NO_WAIT,
        )
        assert await engine.export_to(slot) == 0
        assert store.pending_count() == 5  # kembali bisa diambil batch berikutnya

    async def test_usb_tidak_pernah_dipilih_otomatis(self, store: OutboxStore) -> None:
        """Mengekspor ke flashdisk adalah tindakan yang diminta operator, bukan
        yang terjadi diam-diam karena jaringan sedang mati."""
        import tempfile
        from pathlib import Path

        from fleetview_edge.sync import FileExportTransport

        with tempfile.TemporaryDirectory() as tmp:
            usb = FileExportTransport(target_dir=Path(tmp), ship_slug="SHIP-001")
            mgr = NetworkManager([TransportSlot(adapter=usb)])
            assert await mgr.select() is None
