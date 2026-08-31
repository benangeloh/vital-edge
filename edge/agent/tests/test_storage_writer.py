"""Test StorageWriter, buffer, dan penanganan kegagalan.

Jaminan yang diuji di sini adalah yang paling penting di Phase 3: **kegagalan
penyimpanan tidak pernah menghentikan akuisisi.** InfluxDB yang sedang compaction
tidak boleh membuat kapal buta.
"""

from __future__ import annotations

from uuid import UUID

import pytest

from fleetview_contracts import TelemetryRecord
from fleetview_edge.storage import (
    BoundedRetryBuffer,
    InMemoryTelemetryStore,
    StorageHealth,
    StorageState,
    StorageWriter,
)

SHIP = UUID("11111111-1111-1111-1111-111111111111")
DEVICE = UUID("22222222-2222-2222-2222-222222222222")


def _records(n: int, start: int = 1) -> list[TelemetryRecord]:
    return [
        TelemetryRecord(
            ship_id=SHIP,
            device_id=DEVICE,
            sensor_id="me_port_rpm",
            sequence_number=i,
            timestamp=1_756_612_800_000_000 + i,
            metric="rpm",
            values={"value": float(700 + i)},
            unit="rpm",
        )
        for i in range(start, start + n)
    ]


class TestBoundedRetryBuffer:
    def test_fifo(self) -> None:
        buf = BoundedRetryBuffer(10)
        buf.extend(_records(3))
        assert [r.sequence_number for r in buf.take(3)] == [1, 2, 3]

    def test_membuang_yang_tertua_saat_penuh(self) -> None:
        """Saat penyimpanan bermasalah, kondisi terkini kapal lebih berguna bagi
        operator daripada riwayat beberapa jam lalu."""
        buf = BoundedRetryBuffer(3)
        assert buf.extend(_records(5)) == 2
        assert [r.sequence_number for r in buf.take(3)] == [3, 4, 5]
        assert buf.dropped == 2

    def test_put_back_menjaga_urutan(self) -> None:
        """Record yang gagal ditulis lebih tua daripada apa pun di buffer, jadi
        harus kembali ke depan — kalau ke belakang, grafik menampilkan data lama
        setelah data baru."""
        buf = BoundedRetryBuffer(10)
        buf.extend(_records(2, start=3))
        buf.put_back(_records(2, start=1))
        assert [r.sequence_number for r in buf.take(4)] == [1, 2, 3, 4]

    def test_put_back_saat_penuh_mengorbankan_yang_terbaru(self) -> None:
        buf = BoundedRetryBuffer(3)
        buf.extend(_records(3, start=10))
        assert buf.put_back(_records(2, start=1)) == 2
        assert [r.sequence_number for r in buf.take(3)] == [1, 2, 10]

    def test_take_lebih_banyak_dari_isi(self) -> None:
        buf = BoundedRetryBuffer(10)
        buf.extend(_records(2))
        assert len(buf.take(100)) == 2

    def test_kapasitas_nol_ditolak(self) -> None:
        with pytest.raises(ValueError, match="positif"):
            BoundedRetryBuffer(0)


class TestJalurBahagia:
    async def test_menulis_langsung_saat_sehat(self) -> None:
        store = InMemoryTelemetryStore()
        writer = StorageWriter(store)
        await writer(_records(3))

        assert len(store.records) == 3
        assert writer.written == 3
        assert writer.buffered == 0

    async def test_daftar_kosong_tidak_menyentuh_penyimpanan(self) -> None:
        store = InMemoryTelemetryStore()
        await StorageWriter(store)([])
        assert store.write_attempts == 0


class TestKegagalanSementara:
    async def test_penulisan_gagal_masuk_buffer_bukan_hilang(self) -> None:
        store = InMemoryTelemetryStore(fail_writes_on=[0])
        writer = StorageWriter(store)
        await writer(_records(3))

        assert writer.buffered == 3
        assert writer.written == 0
        assert writer.dropped == 0  # tidak ada yang hilang

    async def test_buffer_dikosongkan_saat_penyimpanan_pulih(self) -> None:
        store = InMemoryTelemetryStore(unavailable=True)
        writer = StorageWriter(store)

        await writer(_records(3, start=1))
        assert writer.buffered == 3

        store.recover()
        await writer(_records(2, start=4))

        assert writer.buffered == 0
        assert [r.sequence_number for r in store.records] == [1, 2, 3, 4, 5]

    async def test_urutan_terjaga_saat_ada_tunggakan(self) -> None:
        """Record baru harus mengantre di belakang buffer, bukan menyalip —
        kalau tidak, grafik menampilkan data lama setelah data baru."""
        store = InMemoryTelemetryStore(unavailable=True)
        writer = StorageWriter(store)

        await writer(_records(2, start=1))
        await writer(_records(2, start=3))
        store.recover()
        await writer(_records(1, start=5))

        assert [r.sequence_number for r in store.records] == [1, 2, 3, 4, 5]

    async def test_flush_memaksa_pengosongan(self) -> None:
        store = InMemoryTelemetryStore(unavailable=True)
        writer = StorageWriter(store)
        await writer(_records(3))

        assert await writer.flush() == 3  # masih mati
        store.recover()
        assert await writer.flush() == 0

    async def test_timestamp_asli_bertahan_melewati_buffering(self) -> None:
        """Inilah alasan timestamp pengukuran dipakai, bukan waktu tulis: data
        yang tertahan harus mendarat di posisi waktu yang benar."""
        store = InMemoryTelemetryStore(unavailable=True)
        writer = StorageWriter(store)
        original = _records(2)
        await writer(original)

        store.recover()
        await writer.flush()

        assert [r.timestamp for r in store.records] == [r.timestamp for r in original]


class TestPenolakanPermanen:
    async def test_data_ditolak_tidak_diulang(self) -> None:
        """Mengulang payload yang ditolak permanen akan menyumbat buffer
        selamanya, dan SEMUA data berikutnya ikut hilang. Lebih baik kehilangan
        satu batch dan mencatatnya sekeras mungkin."""
        store = InMemoryTelemetryStore(reject_writes_on=[0])
        writer = StorageWriter(store)
        await writer(_records(3))

        assert writer.buffered == 0
        assert writer.rejected == 3

    async def test_penolakan_tidak_menghalangi_penulisan_berikutnya(self) -> None:
        store = InMemoryTelemetryStore(reject_writes_on=[0])
        writer = StorageWriter(store)
        await writer(_records(2, start=1))
        await writer(_records(2, start=3))

        assert [r.sequence_number for r in store.records] == [3, 4]


class TestTidakPernahMelempar:
    async def test_kegagalan_sementara_tidak_naik_ke_collector(self) -> None:
        writer = StorageWriter(InMemoryTelemetryStore(unavailable=True))
        await writer(_records(1))  # tidak boleh melempar

    async def test_penolakan_tidak_naik_ke_collector(self) -> None:
        writer = StorageWriter(InMemoryTelemetryStore(reject_writes_on=[0]))
        await writer(_records(1))

    async def test_exception_tak_terduga_pun_ditelan(self) -> None:
        """Jaring pengaman terakhir. Kalau sampai ke sini ada bug di penanganan
        error kita sendiri — tapi Collector tetap tidak boleh ikut mati."""

        class Meledak(InMemoryTelemetryStore):
            async def write(self, records: list[TelemetryRecord]) -> None:
                raise RuntimeError("bug tak terduga")

        await StorageWriter(Meledak())(_records(1))


class TestKesehatan:
    async def test_melaporkan_kedalaman_buffer_dan_yang_terbuang(self) -> None:
        store = InMemoryTelemetryStore(unavailable=True)
        writer = StorageWriter(store, buffer_max_records=2)
        await writer(_records(5))

        health = await writer.health()
        assert health.state is StorageState.UNAVAILABLE
        assert health.buffered_records == 2
        assert health.dropped_records == 3  # data benar-benar hilang

    async def test_sehat_saat_semuanya_normal(self) -> None:
        writer = StorageWriter(InMemoryTelemetryStore())
        await writer(_records(2))

        health = await writer.health()
        assert health.state is StorageState.HEALTHY
        assert health.buffered_records == 0
        assert health.dropped_records == 0
        assert health.counters["written"] == 2

    async def test_health_tidak_melempar_meski_store_rusak(self) -> None:
        class HealthMeledak(InMemoryTelemetryStore):
            async def health(self) -> StorageHealth:
                raise RuntimeError("rusak")

        health = await StorageWriter(HealthMeledak()).health()
        assert health.state is StorageState.UNKNOWN
