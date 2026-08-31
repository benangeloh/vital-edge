"""Integration test bidang ingest terhadap Postgres dan InfluxDB sungguhan.

Jaminan yang diuji di sini adalah yang menentukan apakah data 70 kapal bisa
dipercaya: autentikasi, validasi, idempotensi, dan batas transaksi.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text

from fleetview_central.modules.fleet.models import Sensor, Ship
from fleetview_central.modules.ingest.models import ShipSyncState, SyncBatch
from fleetview_central.modules.ingest.service import IngestService
from fleetview_common import StorageError, now_utc, uuid7
from fleetview_contracts import SCHEMA_VERSION, BatchEnvelope, Quality, Reading

pytestmark = pytest.mark.integration


def _count_from_csv(csv: str) -> int:
    """Ambil nilai dari hasil `|> count()` di CSV beranotasi Flux."""
    lines = [ln for ln in csv.splitlines() if ln.strip()]
    header = next((ln for ln in lines if ln.startswith(",result")), None)
    assert header is not None, f"tidak ada header di CSV:\n{csv}"
    idx = header.split(",").index("_value")
    row = next(ln for ln in lines if ln.startswith(",_result"))
    return int(row.split(",")[idx])


def _readings(n: int, start: int = 1) -> list[Reading]:
    base = int(now_utc().timestamp() * 1_000_000)
    return [
        Reading(
            seq=i,
            ts=base + i * 1_000_000,
            sensor_id="me_port_rpm",
            measurement="rpm",
            fields={"value": float(700 + i)},
            unit="rpm",
            quality=Quality.GOOD,
            tags={"engine": "port"},
        )
        for i in range(start, start + n)
    ]


def _batch(readings: list[Reading], ship_id: UUID, device_id: UUID) -> tuple[BatchEnvelope, bytes]:
    payload = gzip.compress(
        json.dumps([r.model_dump(mode="json") for r in readings], separators=(",", ":")).encode(),
        mtime=0,
    )
    from fleetview_common import from_micros

    return (
        BatchEnvelope(
            schema_version=SCHEMA_VERSION,
            batch_id=uuid7(),
            ship_id=ship_id,
            device_id=device_id,
            sequence_start=readings[0].seq,
            sequence_end=readings[-1].seq,
            first_timestamp=from_micros(min(r.ts for r in readings)),
            last_timestamp=from_micros(max(r.ts for r in readings)),
            record_count=len(readings),
            payload_checksum=f"sha256:{hashlib.sha256(payload).hexdigest()}",
            payload_encoding="gzip+json",
            payload_size=len(payload),
            agent_version="0.1.0",
            config_version="cfg-1",
            created_at=now_utc(),
        ),
        payload,
    )


@pytest.fixture
async def ship(session, ship_id):
    s = Ship(id=ship_id, name="KM Uji Coba", slug="SHIP-001")
    session.add(s)
    session.add(ShipSyncState(ship_id=ship_id))
    await session.flush()
    return s


class TestCommitBatch:
    async def test_batch_tersimpan_dan_ack_diterbitkan(
        self, session, influx, ship, ship_id, device_id
    ) -> None:
        env, payload = _batch(_readings(10), ship_id, device_id)
        ack = await IngestService(session, influx).commit_batch(
            env, payload, ship_id=ship_id, transport="lan"
        )

        assert ack.batch_id == env.batch_id
        assert ack.record_count == 10
        assert ack.last_contiguous_sequence == 10

        row = await session.get(SyncBatch, env.batch_id)
        assert row.status == "committed"
        assert row.ack_payload is not None

    async def test_data_benar_benar_ada_di_influxdb(
        self, session, influx, ship, ship_id, device_id
    ) -> None:
        """Bukan sekadar 'tidak error' — datanya harus bisa dibaca kembali."""
        env, payload = _batch(_readings(5), ship_id, device_id)
        await IngestService(session, influx).commit_batch(
            env, payload, ship_id=ship_id, transport="lan"
        )
        csv = await influx.query_csv(
            f'from(bucket:"telemetry") |> range(start:-2h, stop:5m) '
            f'|> filter(fn:(r) => r.ship_id == "{ship_id}")'
        )
        assert "me_port_rpm" in csv
        assert str(device_id) in csv

    async def test_sensor_baru_didaftarkan_otomatis(
        self, session, influx, ship, ship_id, device_id
    ) -> None:
        """Perangkat lapangan bisa menyajikan sensor yang belum ada di registry
        pusat. Menolaknya berarti membuang data yang sah."""
        env, payload = _batch(_readings(3), ship_id, device_id)
        await IngestService(session, influx).commit_batch(
            env, payload, ship_id=ship_id, transport="lan"
        )
        sensor = await session.scalar(
            select(Sensor).where(Sensor.ship_id == ship_id, Sensor.sensor_id == "me_port_rpm")
        )
        assert sensor is not None
        assert sensor.status == "unregistered"
        assert sensor.metric == "rpm"


class TestIdempotensi:
    async def test_kiriman_ulang_memutar_ack_yang_sama(
        self, session, influx, ship, ship_id, device_id
    ) -> None:
        """Lapis pertama pengaman duplikat: batch_id adalah primary key ledger."""
        env, payload = _batch(_readings(5), ship_id, device_id)
        service = IngestService(session, influx)

        first = await service.commit_batch(env, payload, ship_id=ship_id, transport="lan")
        second = await service.commit_batch(env, payload, ship_id=ship_id, transport="lan")

        assert first.model_dump() == second.model_dump()
        count = await session.scalar(select(text("COUNT(*)")).select_from(SyncBatch))
        assert count == 1

    async def test_kiriman_ulang_tidak_menggandakan_di_influx(
        self, session, influx, ship, ship_id, device_id
    ) -> None:
        """Lapis kedua: identitas titik InfluxDB adalah measurement + tag +
        timestamp, jadi penulisan ulang menimpa di tempat."""
        env, payload = _batch(_readings(3), ship_id, device_id)
        service = IngestService(session, influx)
        await service.commit_batch(env, payload, ship_id=ship_id, transport="lan")
        await service.commit_batch(env, payload, ship_id=ship_id, transport="lan")

        csv = await influx.query_csv(
            f'from(bucket:"telemetry") |> range(start:-2h, stop:5m) '
            f'|> filter(fn:(r) => r.ship_id == "{ship_id}") |> count()'
        )
        # Tiga pembacaan, bukan enam. CSV Flux diurai, bukan dicocokkan
        # sebagai substring — formatnya berubah antar versi.
        assert _count_from_csv(csv) == 3, csv

    async def test_watermark_tidak_maju_dua_kali(
        self, session, influx, ship, ship_id, device_id
    ) -> None:
        env, payload = _batch(_readings(5), ship_id, device_id)
        service = IngestService(session, influx)
        await service.commit_batch(env, payload, ship_id=ship_id, transport="lan")
        await service.commit_batch(env, payload, ship_id=ship_id, transport="lan")

        state = await session.get(ShipSyncState, ship_id)
        assert state.last_contiguous_sequence == 5
        assert state.total_records == 5  # bukan 10


class TestValidasi:
    async def test_checksum_tidak_cocok_ditolak(
        self, session, influx, ship, ship_id, device_id
    ) -> None:
        env, payload = _batch(_readings(5), ship_id, device_id)
        with pytest.raises(Exception, match="checksum"):
            await IngestService(session, influx).commit_batch(
                env, payload + b"rusak", ship_id=ship_id, transport="lan"
            )

    async def test_jumlah_record_tidak_cocok_ditolak(
        self, session, influx, ship, ship_id, device_id
    ) -> None:
        """Envelope menyebut 5 record, payload hanya berisi 3.

        Checksum-nya sengaja dibuat cocok supaya pemeriksaan record_count yang
        diuji, bukan pemeriksaan checksum yang sudah lebih dulu lolos.
        """
        env, _ = _batch(_readings(5), ship_id, device_id)
        _, short_payload = _batch(_readings(3), ship_id, device_id)
        tampered = env.model_copy(
            update={"payload_checksum": f"sha256:{hashlib.sha256(short_payload).hexdigest()}"}
        )
        with pytest.raises(Exception, match="jumlah record tidak cocok"):
            await IngestService(session, influx).commit_batch(
                tampered, short_payload, ship_id=ship_id, transport="lan"
            )

    async def test_kapal_lain_ditolak(self, session, influx, ship, ship_id, device_id) -> None:
        """Tanpa pemeriksaan ini, kredensial satu kapal yang bocor bisa dipakai
        menyuntikkan data atas nama kapal mana pun."""
        env, payload = _batch(_readings(3), uuid4(), device_id)
        with pytest.raises(Exception, match="token terikat"):
            await IngestService(session, influx).commit_batch(
                env, payload, ship_id=ship_id, transport="lan"
            )


class TestWatermark:
    async def test_maju_berurutan(self, session, influx, ship, ship_id, device_id) -> None:
        service = IngestService(session, influx)
        for start in (1, 6, 11):
            env, payload = _batch(_readings(5, start=start), ship_id, device_id)
            ack = await service.commit_batch(env, payload, ship_id=ship_id, transport="lan")
        assert ack.last_contiguous_sequence == 15

    async def test_celah_menahan_watermark_lalu_tertutup(
        self, session, influx, ship, ship_id, device_id
    ) -> None:
        """Celah itu normal: impor USB berisi data lama bisa mendarat lebih dulu
        daripada batch online yang sedang dalam perjalanan."""
        service = IngestService(session, influx)

        env1, p1 = _batch(_readings(5, start=1), ship_id, device_id)
        await service.commit_batch(env1, p1, ship_id=ship_id, transport="lan")

        # Lompat: 11-15 datang lebih dulu, 6-10 belum.
        env3, p3 = _batch(_readings(5, start=11), ship_id, device_id)
        ack3 = await service.commit_batch(env3, p3, ship_id=ship_id, transport="usb")
        assert ack3.last_contiguous_sequence == 5  # tertahan

        state = await session.get(ShipSyncState, ship_id)
        assert state.highest_sequence_seen == 15  # tapi kita tahu ada yang lebih baru

        # Celah tertutup: watermark melompat sampai ujung rantai.
        env2, p2 = _batch(_readings(5, start=6), ship_id, device_id)
        ack2 = await service.commit_batch(env2, p2, ship_id=ship_id, transport="lan")
        assert ack2.last_contiguous_sequence == 15


class TestSesiUpload:
    async def test_upload_berpotongan_lalu_commit(
        self, session, influx, ship, ship_id, device_id
    ) -> None:
        env, payload = _batch(_readings(20), ship_id, device_id)
        service = IngestService(session, influx)

        opened = await service.open_session(env, ship_id=ship_id, chunk_size=32)
        assert opened["received_chunks"] == []
        total = opened["total_chunks"]

        for i in range(total):
            await service.store_chunk(UUID(opened["session_id"]), i, payload[i * 32 : (i + 1) * 32])

        ack = await service.commit_session(UUID(opened["session_id"]), ship_id=ship_id)
        assert ack.record_count == 20

    async def test_membuka_ulang_melaporkan_potongan_yang_sudah_ada(
        self, session, influx, ship, ship_id, device_id
    ) -> None:
        """Inti kemampuan resume: setelah putus, edge hanya mengirim sisanya."""
        env, payload = _batch(_readings(20), ship_id, device_id)
        service = IngestService(session, influx)

        opened = await service.open_session(env, ship_id=ship_id, chunk_size=32)
        sid = UUID(opened["session_id"])
        for i in range(2):
            await service.store_chunk(sid, i, payload[i * 32 : (i + 1) * 32])

        reopened = await service.open_session(env, ship_id=ship_id, chunk_size=32)
        assert reopened["received_chunks"] == [0, 1]
        assert reopened["session_id"] == opened["session_id"]

    async def test_commit_sebelum_lengkap_ditolak(
        self, session, influx, ship, ship_id, device_id
    ) -> None:
        env, payload = _batch(_readings(20), ship_id, device_id)
        service = IngestService(session, influx)
        opened = await service.open_session(env, ship_id=ship_id, chunk_size=32)
        await service.store_chunk(UUID(opened["session_id"]), 0, payload[:32])

        with pytest.raises(Exception, match="belum lengkap"):
            await service.commit_session(UUID(opened["session_id"]), ship_id=ship_id)

    async def test_batch_yang_sudah_committed_dijawab_ack(
        self, session, influx, ship, ship_id, device_id
    ) -> None:
        """ACK yang hilang berbiaya satu round trip, bukan satu upload ulang."""
        env, payload = _batch(_readings(5), ship_id, device_id)
        service = IngestService(session, influx)
        await service.commit_batch(env, payload, ship_id=ship_id, transport="lan")

        reopened = await service.open_session(env, ship_id=ship_id)
        assert reopened["status"] == "already_committed"
        assert reopened["ack"]["batch_id"] == str(env.batch_id)


class TestSyncState:
    async def test_melaporkan_watermark_dan_batch(
        self, session, influx, ship, ship_id, device_id
    ) -> None:
        env, payload = _batch(_readings(5), ship_id, device_id)
        service = IngestService(session, influx)
        await service.commit_batch(env, payload, ship_id=ship_id, transport="lan")

        state = await service.sync_state(ship_id)
        assert state.last_contiguous_sequence == 5
        assert env.batch_id in state.committed_batch_ids
        assert state.has_gap is False


class TestKegagalanInfrastrukturCentral:
    """Kegagalan di sisi central tidak boleh terlihat seperti data buruk.

    Kalau token InfluxDB central salah dan kita melaporkannya sebagai penolakan
    permanen, 70 kapal akan mengarantina data yang sebenarnya baik — dan data itu
    tidak akan pernah dicoba lagi. Ditemukan saat menjalankan server sungguhan.
    """

    async def test_influx_401_dilaporkan_sebagai_bisa_diulang(self) -> None:
        import httpx

        from fleetview_central.platform.influx import InfluxGateway

        gateway = InfluxGateway(
            url="http://influx.test",
            org="fleetview",
            bucket="telemetry",
            token="salah",
            client=httpx.AsyncClient(
                transport=httpx.MockTransport(lambda _r: httpx.Response(401, text="unauthorized")),
                base_url="http://influx.test",
            ),
        )
        with pytest.raises(StorageError) as exc:
            await gateway.write_readings(_readings(1), ship_id=str(uuid4()), device_id=str(uuid4()))
        assert exc.value.retryable is True
        assert exc.value.details["cause"] == "konfigurasi central"

    @pytest.mark.parametrize("status", [400, 422])
    async def test_payload_buruk_tetap_tidak_diulang(self, status: int) -> None:
        """Batch yang benar-benar rusak harus ditolak permanen — kalau diulang,
        satu batch buruk menyumbat antrean kapal selamanya."""
        import httpx

        from fleetview_central.platform.influx import InfluxGateway

        gateway = InfluxGateway(
            url="http://influx.test",
            org="fleetview",
            bucket="telemetry",
            token="t",
            client=httpx.AsyncClient(
                transport=httpx.MockTransport(lambda _r: httpx.Response(status, text="bad")),
                base_url="http://influx.test",
            ),
        )
        with pytest.raises(StorageError) as exc:
            await gateway.write_readings(_readings(1), ship_id=str(uuid4()), device_id=str(uuid4()))
        assert exc.value.retryable is False
