"""Test outbox — source of truth untuk data yang belum disetor."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from fleetview_contracts import SyncPriority, TelemetryRecord
from fleetview_edge.outbox import (
    BatchRow,
    BatchState,
    OutboxSink,
    OutboxStore,
    SqliteSequenceSource,
)

SHIP = UUID("11111111-1111-1111-1111-111111111111")
DEVICE = UUID("22222222-2222-2222-2222-222222222222")


def _records(n: int, start: int = 1, ts_base: int = 1_756_612_800_000_000) -> list[TelemetryRecord]:
    return [
        TelemetryRecord(
            ship_id=SHIP,
            device_id=DEVICE,
            sensor_id="me_port_rpm",
            sequence_number=i,
            timestamp=ts_base + i * 1_000_000,
            metric="rpm",
            values={"value": float(700 + i)},
            unit="rpm",
        )
        for i in range(start, start + n)
    ]


@pytest.fixture
def store() -> OutboxStore:
    return OutboxStore(":memory:", synchronous_full=False)


class TestSequence:
    def test_alokasi_berurutan(self, store: OutboxStore) -> None:
        assert store.allocate_sequence(3) == 1
        assert store.allocate_sequence(2) == 4
        assert store.current_sequence == 5

    def test_bertahan_melewati_restart(self, tmp_path: Path) -> None:
        """Ini alasan sumber sequence in-memory dari Phase 2 harus diganti:
        counter yang mulai dari nol setiap restart membuat nomor terpakai ulang,
        dan data lama bisa tertimpa di central."""
        db = tmp_path / "outbox.db"
        s1 = OutboxStore(db)
        s1.allocate_sequence(100)
        s1.close()

        s2 = OutboxStore(db)
        assert s2.allocate_sequence(1) == 101
        s2.close()

    def test_source_memenuhi_protokol_phase_2(self, store: OutboxStore) -> None:
        src = SqliteSequenceSource(store)
        assert src.allocate(5) == 1
        assert src.current == 5


class TestPenyimpanan:
    def test_append_menyimpan_semua(self, store: OutboxStore) -> None:
        assert store.append(_records(5)) == 5
        assert store.pending_count() == 5

    def test_append_idempoten_pada_sequence_yang_sama(self, store: OutboxStore) -> None:
        """Kalau collector menulis ulang sequence yang sama setelah restart,
        outbox tidak boleh menggandakannya."""
        store.append(_records(3))
        store.append(_records(3))
        assert store.pending_count() == 3

    def test_daftar_kosong_aman(self, store: OutboxStore) -> None:
        assert store.append([]) == 0

    def test_umur_pending_tertua(self, store: OutboxStore) -> None:
        store.append(_records(1, ts_base=0))
        age = store.oldest_pending_age_seconds()
        assert age is not None and age > 0

    def test_umur_none_saat_kosong(self, store: OutboxStore) -> None:
        assert store.oldest_pending_age_seconds() is None


class TestRunBerurutan:
    def test_mengambil_baris_berurutan(self, store: OutboxStore) -> None:
        store.append(_records(10))
        run = store.claim_contiguous_run(limit=5)
        assert [r.sequence for r in run] == [1, 2, 3, 4, 5]

    def test_celah_menjadi_batas_batch(self, store: OutboxStore) -> None:
        """Celah bisa muncul kalau proses mati antara alokasi sequence dan
        penyimpanan record. Envelope menuntut rentang tanpa celah, jadi celah
        harus mengakhiri batch — bukan dianggap kehilangan data."""
        store.append(_records(3, start=1))
        store.append(_records(3, start=10))  # celah 4..9
        run = store.claim_contiguous_run(limit=100)
        assert [r.sequence for r in run] == [1, 2, 3]

    def test_prioritas_tidak_dicampur_dalam_satu_batch(self, store: OutboxStore) -> None:
        """Semua baris dalam satu batch berbagi prioritas, supaya pengiriman
        berdasarkan prioritas benar-benar berlaku per batch."""
        store.append(_records(2, start=1), priority=SyncPriority.CRITICAL)
        store.append(_records(2, start=3), priority=SyncPriority.RAW)
        run = store.claim_contiguous_run(limit=100)
        assert [r.sequence for r in run] == [1, 2]
        assert all(r.priority is SyncPriority.CRITICAL for r in run)

    def test_prioritas_kritis_lebih_dulu(self, store: OutboxStore) -> None:
        store.append(_records(2, start=1), priority=SyncPriority.RAW)
        store.append(_records(2, start=3), priority=SyncPriority.CRITICAL)
        run = store.claim_contiguous_run(limit=100)
        assert run[0].priority is SyncPriority.CRITICAL

    def test_filter_prioritas_maksimum(self, store: OutboxStore) -> None:
        """Link seluler bermeteran hanya melewatkan sampai prioritas tertentu."""
        store.append(_records(2, start=1), priority=SyncPriority.RAW)
        assert store.claim_contiguous_run(limit=10, max_priority=SyncPriority.SUMMARY) == []


class TestSiklusBatch:
    def _make_batch(self, store: OutboxStore, seqs: list[int]) -> str:
        batch_id = "018f2c00-0000-7000-8000-000000000001"
        store.create_batch(
            BatchRow(
                batch_id=batch_id,
                sequence_start=seqs[0],
                sequence_end=seqs[-1],
                first_timestamp=1,
                last_timestamp=2,
                record_count=len(seqs),
                payload_checksum="sha256:" + "a" * 64,
                schema_version="1.0",
                priority=SyncPriority.RAW,
                state=BatchState.BUILT,
            ),
            seqs,
        )
        return batch_id

    def test_create_batch_mengikat_baris(self, store: OutboxStore) -> None:
        store.append(_records(3))
        bid = self._make_batch(store, [1, 2, 3])
        assert store.pending_count() == 0
        assert len(store.batch_records(bid)) == 3

    def test_ack_memindahkan_baris_ke_acked(self, store: OutboxStore) -> None:
        store.append(_records(3))
        bid = self._make_batch(store, [1, 2, 3])
        store.mark_acked(bid, transport="lan")

        batch = store.batch(bid)
        assert batch is not None and batch.state is BatchState.ACKED
        assert store.stats()["records_acked"] == 3

    def test_release_mengembalikan_baris_ke_pending(self, store: OutboxStore) -> None:
        store.append(_records(3))
        bid = self._make_batch(store, [1, 2, 3])
        store.release_batch(bid)
        assert store.pending_count() == 3
        assert store.batch(bid) is None

    def test_batch_gagal_tidak_menghapus_data(self, store: OutboxStore) -> None:
        """Batch yang gagal permanen dikarantina supaya tidak menyumbat antrean,
        tetapi barisnya tetap ada. Dibuang diam-diam adalah kehilangan data yang
        tidak terlihat."""
        store.append(_records(3))
        bid = self._make_batch(store, [1, 2, 3])
        store.mark_failed(bid, error="checksum tidak cocok")

        assert len(store.batch_records(bid)) == 3
        batch = store.batch(bid)
        assert batch is not None and batch.state is BatchState.FAILED

    def test_batch_siap_menghormati_jadwal_percobaan(self, store: OutboxStore) -> None:
        store.append(_records(3))
        bid = self._make_batch(store, [1, 2, 3])
        # Nilai realistis: timestamp mikrodetik ada di kisaran 1,7e15.
        future = 2_000_000_000_000_000
        store.mark_attempt(bid, transport="lan", next_attempt_at=future, error="mati")
        assert store.batches_ready(now_us=future - 1) == []
        assert len(store.batches_ready(now_us=future + 1)) == 1


class TestPemangkasan:
    def test_hanya_memangkas_yang_sudah_acked(self, store: OutboxStore) -> None:
        """Baris pending tidak pernah dipangkas, berapa pun tekanan disk —
        kehilangan data yang belum tersetor adalah kegagalan yang justru ingin
        dicegah seluruh sistem ini."""
        store.append(_records(3, start=1, ts_base=0))
        store.append(_records(3, start=10, ts_base=0))
        store.create_batch(
            BatchRow(
                batch_id="b1",
                sequence_start=1,
                sequence_end=3,
                first_timestamp=1,
                last_timestamp=2,
                record_count=3,
                payload_checksum="sha256:" + "a" * 64,
                schema_version="1.0",
                priority=SyncPriority.RAW,
                state=BatchState.BUILT,
            ),
            [1, 2, 3],
        )
        store.mark_acked("b1", transport="lan")

        # Ambang jauh di masa depan supaya masa tenggang pasti terlampaui.
        removed = store.prune_acked(older_than_us=2_000_000_000_000_000)
        assert removed == 3
        assert store.pending_count() == 3  # yang pending selamat

    def test_tenggang_dihitung_dari_ack_bukan_waktu_pengukuran(self, store: OutboxStore) -> None:
        """Kapal yang offline 30 hari menyetor data berumur 30 hari yang baru
        saja di-ACK. Kalau tenggang dihitung dari waktu pengukuran, seluruh
        jendela replay lokal hilang tepat saat paling dibutuhkan."""
        store.append(_records(3, ts_base=0))  # data "lama"
        store.create_batch(
            BatchRow(
                batch_id="b_old",
                sequence_start=1,
                sequence_end=3,
                first_timestamp=1,
                last_timestamp=2,
                record_count=3,
                payload_checksum="sha256:" + "a" * 64,
                schema_version="1.0",
                priority=SyncPriority.RAW,
                state=BatchState.BUILT,
            ),
            [1, 2, 3],
        )
        store.mark_acked("b_old", transport="lan")  # baru saja di-ACK

        # Tenggang 7 hari dari sekarang: belum terlampaui, meski datanya tua.
        from fleetview_common import now_micros

        assert store.prune_acked(older_than_us=now_micros() - 7 * 86_400_000_000) == 0

    def test_masa_tenggang_dihormati(self, store: OutboxStore) -> None:
        store.append(_records(2))
        store.create_batch(
            BatchRow(
                batch_id="b1",
                sequence_start=1,
                sequence_end=2,
                first_timestamp=1,
                last_timestamp=2,
                record_count=2,
                payload_checksum="sha256:" + "a" * 64,
                schema_version="1.0",
                priority=SyncPriority.RAW,
                state=BatchState.BUILT,
            ),
            [1, 2],
        )
        store.mark_acked("b1", transport="lan")
        assert store.prune_acked(older_than_us=0) == 0  # masih dalam masa tenggang


class TestWriteBarrier:
    async def test_sink_menulis_durable(self, tmp_path: Path) -> None:
        db = tmp_path / "outbox.db"
        store = OutboxStore(db)
        await OutboxSink(store)(_records(5))
        store.close()

        reopened = OutboxStore(db)
        assert reopened.pending_count() == 5
        reopened.close()

    async def test_kegagalan_hilir_tidak_menghilangkan_data(self, store: OutboxStore) -> None:
        """Mulai titik barrier, data sudah aman. Apa pun yang gagal di bawahnya
        bukan kehilangan data, hanya keterlambatan."""

        async def meledak(_records: list[TelemetryRecord]) -> None:
            raise RuntimeError("InfluxDB mati")

        await OutboxSink(store, downstream=meledak)(_records(3))
        assert store.pending_count() == 3

    async def test_meneruskan_ke_hilir(self, store: OutboxStore) -> None:
        seen: list[TelemetryRecord] = []

        async def downstream(rs: list[TelemetryRecord]) -> None:
            seen.extend(rs)

        await OutboxSink(store, downstream=downstream)(_records(3))
        assert len(seen) == 3
