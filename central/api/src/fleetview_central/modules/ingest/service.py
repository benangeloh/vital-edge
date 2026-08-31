"""Layanan ingest — jalur kritis Phase 5.

Empat jaminan yang harus dipenuhi, dan urutan operasinya menentukan semuanya:

1. **Terautentikasi.** Token device terikat pada satu `ship_id`; envelope batch
   diverifikasi cocok dengannya.
2. **Tervalidasi.** Checksum, jumlah record, dan kerunutan rentang sequence
   diperiksa sebelum satu byte pun disimpan.
3. **Idempoten.** `batch_id` adalah primary key ledger. Kiriman ulang batch yang
   sudah committed memutar ulang ACK tersimpan.
4. **Transaksional di tingkat yang tepat.** PostgreSQL lebih dulu, InfluxDB
   kemudian, ACK terakhir. Kalau penulisan InfluxDB gagal, batch tetap
   `staging` dan **tidak ada ACK yang diterbitkan** — kapal akan mengulang.
   Central tidak pernah mengklaim memiliki data yang sebenarnya tidak ada padanya.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from fleetview_central.modules.fleet.models import Sensor
from fleetview_central.modules.ingest.models import (
    ShipSyncState,
    SyncBatch,
    UploadChunk,
    UploadSession,
)
from fleetview_central.platform.influx import InfluxGateway
from fleetview_common import ValidationError, get_logger, log_context, now_utc
from fleetview_contracts import Ack, BatchEnvelope, BatchStatus, Reading, SyncState

__all__ = ["IngestService"]

log = get_logger(__name__)

SESSION_TTL_HOURS = 24
DEFAULT_CHUNK_SIZE = 262_144


class IngestService:
    def __init__(self, session: AsyncSession, influx: InfluxGateway) -> None:
        self._db = session
        self._influx = influx

    # -- validasi -----------------------------------------------------------

    @staticmethod
    def _verify_envelope(envelope: BatchEnvelope, ship_id: UUID) -> None:
        """Periksa envelope sebelum menyentuh apa pun.

        Pemeriksaan ship_id itu penting: tanpa itu, kredensial satu kapal yang
        bocor bisa dipakai menyuntikkan data atas nama kapal mana pun.
        """
        if envelope.ship_id != ship_id:
            raise ValidationError(
                f"envelope untuk kapal {envelope.ship_id}, tetapi token terikat pada {ship_id}",
                code="ingest.ship_mismatch",
                details={"envelope_ship": str(envelope.ship_id), "token_ship": str(ship_id)},
            )

    @staticmethod
    def _verify_payload(envelope: BatchEnvelope, payload: bytes) -> list[Reading]:
        """Verifikasi checksum lalu urai. Checksum diperiksa lebih dulu."""
        actual = f"sha256:{hashlib.sha256(payload).hexdigest()}"
        if actual != envelope.payload_checksum:
            raise ValidationError(
                "checksum payload tidak cocok — data rusak di perjalanan",
                code="ingest.checksum_mismatch",
                details={"expected": envelope.payload_checksum, "actual": actual},
            )

        try:
            readings = [Reading.model_validate(d) for d in json.loads(gzip.decompress(payload))]
        except Exception as exc:
            raise ValidationError(
                f"payload tidak bisa diurai: {exc}",
                code="ingest.payload_undecodable",
                details={"encoding": envelope.payload_encoding},
            ) from exc

        if len(readings) != envelope.record_count:
            raise ValidationError(
                f"jumlah record tidak cocok: envelope menyebut {envelope.record_count}, "
                f"payload berisi {len(readings)}",
                code="ingest.record_count_mismatch",
            )

        sequences = [r.seq for r in readings]
        expected = list(range(envelope.sequence_start, envelope.sequence_end + 1))
        if sorted(sequences) != expected:
            raise ValidationError(
                "sequence di payload tidak berurutan atau tidak cocok dengan envelope",
                code="ingest.sequence_mismatch",
                details={"start": envelope.sequence_start, "end": envelope.sequence_end},
            )
        return readings

    # -- idempotensi --------------------------------------------------------

    async def existing_ack(self, batch_id: UUID) -> Ack | None:
        """ACK tersimpan untuk batch yang sudah committed, kalau ada.

        Inilah lapis pertama pengaman duplikat: kiriman ulang mendapat ACK yang
        sama persis tanpa data disimpan dua kali.
        """
        row = await self._db.scalar(
            select(SyncBatch).where(SyncBatch.batch_id == batch_id, SyncBatch.status == "committed")
        )
        if row is None or row.ack_payload is None:
            return None
        return Ack.model_validate(row.ack_payload)

    # -- commit -------------------------------------------------------------

    async def commit_batch(
        self, envelope: BatchEnvelope, payload: bytes, *, ship_id: UUID, transport: str | None
    ) -> Ack:
        """Validasi, simpan, dan terbitkan ACK.

        Idempoten: memanggilnya berulang untuk batch yang sama menghasilkan ACK
        yang sama dan tidak menyimpan data dua kali.
        """
        with log_context(batch_id=str(envelope.batch_id), ship_id=str(ship_id)):
            self._verify_envelope(envelope, ship_id)

            replay = await self.existing_ack(envelope.batch_id)
            if replay is not None:
                log.info("ingest.duplicate_batch", note="memutar ulang ACK tersimpan")
                return replay

            readings = self._verify_payload(envelope, payload)

            # Ledger lebih dulu, dalam transaksi ini. ON CONFLICT menangani dua
            # percobaan yang datang bersamaan — yang kedua tidak menggandakan.
            await self._db.execute(
                pg_insert(SyncBatch)
                .values(
                    batch_id=envelope.batch_id,
                    ship_id=ship_id,
                    device_id=envelope.device_id,
                    sequence_start=envelope.sequence_start,
                    sequence_end=envelope.sequence_end,
                    first_timestamp=envelope.first_timestamp,
                    last_timestamp=envelope.last_timestamp,
                    record_count=envelope.record_count,
                    payload_checksum=envelope.payload_checksum,
                    payload_encoding=envelope.payload_encoding,
                    payload_size=envelope.payload_size,
                    schema_version=envelope.schema_version,
                    agent_version=envelope.agent_version,
                    config_version=envelope.config_version,
                    transport=transport,
                    status="staging",
                )
                .on_conflict_do_nothing(index_elements=["batch_id"])
            )
            await self._db.flush()

            # InfluxDB kedua. Kalau gagal, exception naik, transaksi di-rollback,
            # tidak ada ACK — dan kapal akan mengulang. Central tidak pernah
            # mengklaim punya data yang sebenarnya tidak tersimpan.
            written = await self._influx.write_readings(
                readings, ship_id=str(ship_id), device_id=str(envelope.device_id)
            )

            await self._register_sensors(ship_id, readings)
            watermark = await self._advance_watermark(ship_id, envelope)

            ack = Ack(
                batch_id=envelope.batch_id,
                status=BatchStatus.COMMITTED,
                last_contiguous_sequence=watermark,
                server_received_at=now_utc(),
                record_count=envelope.record_count,
            )
            await self._db.execute(
                update(SyncBatch)
                .where(SyncBatch.batch_id == envelope.batch_id)
                .values(
                    status="committed",
                    committed_at=now_utc(),
                    ack_payload=ack.model_dump(mode="json"),
                )
            )

            log.info(
                "ingest.batch_committed",
                records=envelope.record_count,
                points_written=written,
                last_contiguous=watermark,
                transport=transport,
            )
            return ack

    async def _register_sensors(self, ship_id: UUID, readings: list[Reading]) -> None:
        """Daftarkan sensor yang belum dikenal, dan perbarui waktu terlihat.

        Sensor baru **tidak** ditolak. Perangkat lapangan bisa menyajikan sensor
        yang belum ada di registry pusat, dan menolaknya berarti membuang data
        yang sah. Operator bisa memberinya nama nanti.
        """
        seen: dict[str, Reading] = {}
        for r in readings:
            seen.setdefault(r.sensor_id, r)
        if not seen:
            return

        now = now_utc()
        for sensor_id, sample in seen.items():
            await self._db.execute(
                pg_insert(Sensor)
                .values(
                    ship_id=ship_id,
                    sensor_id=sensor_id,
                    metric=sample.measurement,
                    unit=sample.unit,
                    status="unregistered",
                    tags=dict(sample.tags),
                    last_seen_at=now,
                )
                .on_conflict_do_update(
                    index_elements=["ship_id", "sensor_id"], set_={"last_seen_at": now}
                )
            )

    async def _advance_watermark(self, ship_id: UUID, envelope: BatchEnvelope) -> int:
        """Majukan watermark contiguous, lalu ikuti rantai batch berikutnya.

        Celah itu normal: impor USB berisi data lama bisa mendarat lebih dulu
        daripada batch online yang sedang dalam perjalanan. Karena itu watermark
        hanya maju sejauh rantai yang benar-benar bersambung, dan sisanya
        menunggu — bukan dianggap hilang.
        """
        state = await self._db.get(ShipSyncState, ship_id)
        if state is None:
            state = ShipSyncState(ship_id=ship_id)
            self._db.add(state)
            await self._db.flush()

        state.highest_sequence_seen = max(state.highest_sequence_seen, envelope.sequence_end)
        state.last_batch_received_at = now_utc()
        state.last_telemetry_timestamp = envelope.last_timestamp
        state.total_batches += 1
        state.total_records += envelope.record_count
        state.agent_version = envelope.agent_version
        state.config_version = envelope.config_version
        state.connection_state = "online"

        if envelope.sequence_start == state.last_contiguous_sequence + 1:
            state.last_contiguous_sequence = envelope.sequence_end
            # Ikuti rantai: batch yang tadinya menunggu karena celah kini bisa
            # ikut terhitung.
            while True:
                nxt = await self._db.scalar(
                    select(SyncBatch).where(
                        SyncBatch.ship_id == ship_id,
                        SyncBatch.status == "committed",
                        SyncBatch.sequence_start == state.last_contiguous_sequence + 1,
                    )
                )
                if nxt is None:
                    break
                state.last_contiguous_sequence = nxt.sequence_end

        await self._db.flush()
        return state.last_contiguous_sequence

    # -- sync state ---------------------------------------------------------

    async def sync_state(self, ship_id: UUID) -> SyncState:
        state = await self._db.get(ShipSyncState, ship_id)
        committed = await self._db.scalars(
            select(SyncBatch.batch_id)
            .where(SyncBatch.ship_id == ship_id, SyncBatch.status == "committed")
            .order_by(SyncBatch.sequence_start.desc())
            .limit(200)
        )
        return SyncState(
            ship_id=ship_id,
            last_contiguous_sequence=state.last_contiguous_sequence if state else 0,
            highest_sequence_seen=state.highest_sequence_seen if state else 0,
            committed_batch_ids=list(committed),
            server_time=now_utc(),
        )

    # -- sesi upload --------------------------------------------------------

    async def open_session(
        self, envelope: BatchEnvelope, *, ship_id: UUID, chunk_size: int = DEFAULT_CHUNK_SIZE
    ) -> dict[str, Any]:
        """Buka atau lanjutkan sesi upload berpotongan."""
        self._verify_envelope(envelope, ship_id)

        replay = await self.existing_ack(envelope.batch_id)
        if replay is not None:
            return {"status": "already_committed", "ack": replay.model_dump(mode="json")}

        existing = await self._db.scalar(
            select(UploadSession).where(UploadSession.batch_id == envelope.batch_id)
        )
        if existing is None:
            total = max(1, (envelope.payload_size + chunk_size - 1) // chunk_size)
            existing = UploadSession(
                batch_id=envelope.batch_id,
                ship_id=ship_id,
                envelope=envelope.model_dump(mode="json"),
                chunk_size=chunk_size,
                total_chunks=total,
                expires_at=datetime.now(UTC) + timedelta(hours=SESSION_TTL_HOURS),
            )
            self._db.add(existing)
            await self._db.flush()

        received = await self._db.scalars(
            select(UploadChunk.chunk_index).where(UploadChunk.session_id == existing.id)
        )
        return {
            "session_id": str(existing.id),
            "batch_id": str(existing.batch_id),
            "chunk_size": existing.chunk_size,
            "total_chunks": existing.total_chunks,
            "received_chunks": sorted(received),
        }

    async def store_chunk(self, session_id: UUID, index: int, data: bytes) -> None:
        session = await self._db.get(UploadSession, session_id)
        if session is None:
            raise ValidationError(
                "sesi upload tidak ditemukan atau sudah kedaluwarsa",
                code="ingest.session_not_found",
            )
        if not 0 <= index < session.total_chunks:
            raise ValidationError(
                f"indeks potongan {index} di luar 0..{session.total_chunks - 1}",
                code="ingest.chunk_out_of_range",
            )
        # Potongan yang sama dikirim ulang setelah putus itu wajar — timpa saja.
        await self._db.execute(
            pg_insert(UploadChunk)
            .values(session_id=session_id, chunk_index=index, data=data)
            .on_conflict_do_update(
                index_elements=["session_id", "chunk_index"], set_={"data": data}
            )
        )
        await self._db.flush()

    async def commit_session(self, session_id: UUID, *, ship_id: UUID) -> Ack:
        """Rakit ulang potongan, verifikasi, lalu commit."""
        session = await self._db.get(UploadSession, session_id)
        if session is None:
            raise ValidationError(
                "sesi upload tidak ditemukan atau sudah kedaluwarsa",
                code="ingest.session_not_found",
            )

        count = await self._db.scalar(
            select(func.count())
            .select_from(UploadChunk)
            .where(UploadChunk.session_id == session_id)
        )
        if (count or 0) != session.total_chunks:
            raise ValidationError(
                f"upload belum lengkap: {count} dari {session.total_chunks} potongan",
                code="ingest.incomplete_upload",
                details={"received": count, "expected": session.total_chunks},
            )

        chunks = await self._db.scalars(
            select(UploadChunk)
            .where(UploadChunk.session_id == session_id)
            .order_by(UploadChunk.chunk_index)
        )
        payload = b"".join(c.data for c in chunks)
        envelope = BatchEnvelope.model_validate(session.envelope)

        ack = await self.commit_batch(envelope, payload, ship_id=ship_id, transport="chunked")
        await self._db.delete(session)  # potongan ikut terhapus lewat cascade
        return ack
