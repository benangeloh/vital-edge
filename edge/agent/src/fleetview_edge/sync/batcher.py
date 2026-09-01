"""Pembentukan batch dari outbox.

Batch adalah satuan sinkronisasi sekaligus satuan idempotensi. `batch_id` dibuat
di kapal dan menjadi primary key ledger di central, sehingga pengiriman ulang
menghasilkan konflik primary key — bukan data ganda.
"""

from __future__ import annotations

import gzip
import json
from uuid import UUID

from fleetview_common import from_micros, get_logger, now_utc, uuid7
from fleetview_contracts import SCHEMA_VERSION, BatchEnvelope, SyncPriority
from fleetview_contracts.reading import Reading
from fleetview_edge.outbox import BatchRow, BatchState, OutboxStore, checksum_of

__all__ = ["Batcher", "BuiltBatch", "decode_payload", "encode_payload"]

log = get_logger(__name__)

PAYLOAD_ENCODING = "gzip+json"
"""gzip dan JSON, bukan zstd dan msgpack.

gzip ada di stdlib — tidak menambah dependency pada Pi yang berjalan 24/7 — dan
JSON bisa dibaca manusia. Yang kedua itu berharga saat teknisi membuka berkas
export USB di kapal untuk mencari tahu kenapa sesuatu tidak sampai. Telemetry
JSON ter-gzip menyusut sekitar 85%, cukup dekat dengan zstd untuk data sebesar ini.
"""


def encode_payload(readings: list[Reading]) -> bytes:
    body = json.dumps([r.model_dump(mode="json") for r in readings], separators=(",", ":")).encode(
        "utf-8"
    )
    # mtime=0 supaya byte-nya deterministik: payload yang sama menghasilkan
    # checksum yang sama, dan pengiriman ulang bisa diverifikasi identik.
    return gzip.compress(body, mtime=0)


def decode_payload(payload: bytes) -> list[Reading]:
    return [Reading.model_validate(d) for d in json.loads(gzip.decompress(payload))]


class BuiltBatch:
    """Batch yang siap dikirim: envelope, payload, dan chunk asalnya."""

    __slots__ = ("chunk_starts", "envelope", "payload")

    def __init__(self, envelope: BatchEnvelope, payload: bytes, chunk_starts: list[int]) -> None:
        self.envelope = envelope
        self.payload = payload
        self.chunk_starts = chunk_starts


class Batcher:
    """Membentuk batch dari baris outbox yang pending.

    Args:
        store: outbox.
        max_records: batas jumlah record per batch.
        max_bytes: batas ukuran payload terkompresi.
        agent_version, config_version: ikut di envelope, supaya saat sebuah
            kapal berperilaku aneh kita tahu versi apa yang menghasilkannya.
    """

    def __init__(
        self,
        store: OutboxStore,
        *,
        max_records: int = 500,
        max_bytes: int = 1_048_576,
        agent_version: str = "0.0.0",
        config_version: str = "local",
    ) -> None:
        self._store = store
        self._max_records = max_records
        self._max_bytes = max_bytes
        self._agent_version = agent_version
        self._config_version = config_version

    def build_next(self, *, max_priority: SyncPriority | None = None) -> BuiltBatch | None:
        """Bentuk satu batch dari baris pending tertua yang prioritasnya tertinggi.

        Mengembalikan None bila tidak ada yang pending.
        """
        chunks = self._store.claim_contiguous_run(
            limit=self._max_records, max_priority=max_priority
        )
        if not chunks:
            return None

        # Kecilkan sampai muat batas byte. Dilakukan setelah kompresi karena
        # rasio kompresi telemetry sangat bergantung isinya — menebak dari
        # jumlah record akan meleset jauh.
        #
        # Pengecilan membuang chunk utuh, tidak pernah memotong di tengah chunk:
        # state melekat pada chunk, jadi membatch separuh chunk akan menandai
        # seluruhnya terkirim padahal sebagiannya tidak.
        while chunks:
            records = [r for c in chunks for r in c.records]
            payload = encode_payload([r.to_reading() for r in records])
            if len(payload) <= self._max_bytes or len(chunks) == 1:
                break
            chunks = chunks[: len(chunks) // 2]

        first_seq = chunks[0].sequence_start
        last_seq = chunks[-1].sequence_end
        priority = chunks[0].priority
        batch_id = uuid7()
        envelope = BatchEnvelope(
            schema_version=SCHEMA_VERSION,
            batch_id=batch_id,
            ship_id=records[0].ship_id,
            device_id=records[0].device_id,
            sequence_start=first_seq,
            sequence_end=last_seq,
            first_timestamp=from_micros(min(r.timestamp for r in records)),
            last_timestamp=from_micros(max(r.timestamp for r in records)),
            record_count=len(records),
            payload_checksum=checksum_of(payload),
            payload_encoding=PAYLOAD_ENCODING,
            payload_size=len(payload),
            agent_version=self._agent_version,
            config_version=self._config_version,
            created_at=now_utc(),
        )

        chunk_starts = [c.sequence_start for c in chunks]
        self._store.create_batch(
            BatchRow(
                batch_id=str(batch_id),
                sequence_start=first_seq,
                sequence_end=last_seq,
                first_timestamp=min(r.timestamp for r in records),
                last_timestamp=max(r.timestamp for r in records),
                record_count=len(records),
                payload_checksum=envelope.payload_checksum,
                schema_version=SCHEMA_VERSION,
                priority=priority,
                state=BatchState.BUILT,
            ),
            chunk_starts,
        )

        log.info(
            "sync.batch_built",
            batch_id=str(batch_id),
            records=len(records),
            chunks=len(chunks),
            bytes=len(payload),
            priority=int(priority),
            sequence_start=first_seq,
            sequence_end=last_seq,
        )
        return BuiltBatch(envelope, payload, chunk_starts)

    def rebuild(self, batch_id: str) -> BuiltBatch | None:
        """Susun ulang batch yang sudah ada dari outbox, untuk percobaan ulang.

        Payload dibentuk ulang dari baris yang sama, sehingga checksum-nya
        identik dengan percobaan sebelumnya — itulah yang membuat resume di
        tengah transfer bisa dilanjutkan alih-alih diulang dari nol.
        """
        row = self._store.batch(batch_id)
        if row is None:
            return None
        records = self._store.batch_records(batch_id)
        if not records:
            return None

        payload = encode_payload([r.to_reading() for r in records])
        envelope = BatchEnvelope(
            schema_version=row.schema_version,
            batch_id=UUID(batch_id),
            ship_id=records[0].ship_id,
            device_id=records[0].device_id,
            sequence_start=row.sequence_start,
            sequence_end=row.sequence_end,
            first_timestamp=from_micros(row.first_timestamp),
            last_timestamp=from_micros(row.last_timestamp),
            record_count=row.record_count,
            payload_checksum=row.payload_checksum,
            payload_encoding=PAYLOAD_ENCODING,
            payload_size=len(payload),
            agent_version=self._agent_version,
            config_version=self._config_version,
            created_at=now_utc(),
        )
        return BuiltBatch(envelope, payload, [])
