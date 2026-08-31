"""OutboxStore — penyimpanan durable untuk data yang belum disetor.

API-nya sinkron. SQLite sendiri sinkron, dan operasi di sini berukuran
sub-milidetik; membungkusnya dengan aiosqlite hanya menambah dependency tanpa
manfaat nyata. Pemanggil dari konteks async memakai `asyncio.to_thread`.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from fleetview_common import get_logger, now_micros
from fleetview_contracts import SyncPriority, TelemetryRecord
from fleetview_edge.outbox.models import BatchRow, BatchState, OutboxRow, RecordState
from fleetview_edge.outbox.schema import DDL

__all__ = ["OutboxStore", "SqliteSequenceSource"]

log = get_logger(__name__)

SEQUENCE_COUNTER = "sequence"


class OutboxStore:
    """Outbox berbasis SQLite.

    Args:
        path: berkas database. `:memory:` untuk pengujian.
        synchronous_full: bila True (default), setiap commit di-fsync. Itulah
            yang membuat data selamat dari mati listrik — dan itu satu-satunya
            alasan outbox ini ada. Matikan hanya untuk pengujian.
    """

    def __init__(self, path: Path | str, *, synchronous_full: bool = True) -> None:
        self._path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(DDL)
        # FULL berarti fsync di setiap commit. Lebih lambat, tetapi tanpa itu
        # sebuah pembacaan yang "sudah tersimpan" bisa hilang saat listrik kapal
        # putus — dan seluruh premis offline-first ikut hilang bersamanya.
        self._conn.execute(f"PRAGMA synchronous = {'FULL' if synchronous_full else 'NORMAL'}")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- sequence -----------------------------------------------------------

    def allocate_sequence(self, count: int) -> int:
        """Pesan `count` nomor berurutan. Kembalikan yang pertama.

        Alokasi di-commit sebelum kembali, sehingga nomor tidak pernah terpakai
        ulang setelah restart. Kalau proses mati antara alokasi dan penyimpanan
        record, nomor itu hilang — meninggalkan celah. Celah ditangani sebagai
        batas batch oleh Batcher, bukan dianggap kehilangan data.
        """
        if count <= 0:
            raise ValueError(f"count harus positif, dapat {count}")
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO counters(name, value) VALUES(?, 0) ON CONFLICT(name) DO NOTHING",
                (SEQUENCE_COUNTER,),
            )
            row = self._conn.execute(
                "UPDATE counters SET value = value + ? WHERE name = ? RETURNING value",
                (count, SEQUENCE_COUNTER),
            ).fetchone()
            last = int(row["value"])
            return last - count + 1

    @property
    def current_sequence(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM counters WHERE name = ?", (SEQUENCE_COUNTER,)
            ).fetchone()
            return int(row["value"]) if row else 0

    # -- penulisan ----------------------------------------------------------

    def append(
        self, records: list[TelemetryRecord], *, priority: SyncPriority | None = None
    ) -> int:
        """Simpan record secara durable. **Ini write barrier.**

        Sekali fungsi ini kembali tanpa exception, data selamat dari mati
        listrik. Semua yang terjadi setelahnya — penulisan InfluxDB,
        sinkronisasi — berada di hilir dan boleh gagal.
        """
        if not records:
            return 0
        rows = [
            (
                r.sequence_number,
                r.timestamp,
                int(priority if priority is not None else SyncPriority.RAW),
                r.model_dump_json(),
                RecordState.PENDING.value,
            )
            for r in records
        ]
        with self._lock, self._conn:
            self._conn.executemany(
                "INSERT OR IGNORE INTO outbox"
                "(sequence, captured_at, priority, payload, state) VALUES(?,?,?,?,?)",
                rows,
            )
        return len(rows)

    # -- pembacaan ----------------------------------------------------------

    def pending_count(self, *, max_priority: SyncPriority | None = None) -> int:
        sql = "SELECT COUNT(*) AS n FROM outbox WHERE state = ?"
        params: list[Any] = [RecordState.PENDING.value]
        if max_priority is not None:
            sql += " AND priority <= ?"
            params.append(int(max_priority))
        with self._lock:
            return int(self._conn.execute(sql, params).fetchone()["n"])

    def oldest_pending_age_seconds(self) -> float | None:
        """Umur data belum terkirim yang paling lama.

        Indikator paling jujur untuk "seberapa tertinggal kapal ini" — jauh
        lebih berguna daripada jumlah baris, karena 10.000 baris berumur satu
        menit dan 10 baris berumur tiga minggu adalah situasi yang sangat berbeda.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT MIN(captured_at) AS t FROM outbox WHERE state = ?",
                (RecordState.PENDING.value,),
            ).fetchone()
        if row is None or row["t"] is None:
            return None
        return (now_micros() - int(row["t"])) / 1_000_000

    def claim_contiguous_run(
        self, *, limit: int, max_priority: SyncPriority | None = None
    ) -> list[OutboxRow]:
        """Ambil sampai `limit` baris pending yang **sequence-nya berurutan**.

        Kerunutan itu wajib, bukan optimasi: envelope batch menuntut
        `record_count == sequence_end - sequence_start + 1`. Celah — yang bisa
        muncul kalau proses mati antara alokasi sequence dan penyimpanan —
        diperlakukan sebagai batas batch, jadi batch berikutnya mulai setelah
        celah itu.

        Semua baris dalam satu batch berbagi prioritas yang sama, sehingga
        pengiriman berdasarkan prioritas benar-benar berlaku per batch.
        """
        sql = (
            "SELECT sequence, captured_at, priority, payload, state, batch_id "
            "FROM outbox WHERE state = ?"
        )
        params: list[Any] = [RecordState.PENDING.value]
        if max_priority is not None:
            sql += " AND priority <= ?"
            params.append(int(max_priority))
        sql += " ORDER BY priority ASC, sequence ASC LIMIT ?"
        params.append(limit)

        with self._lock:
            raw = self._conn.execute(sql, params).fetchall()

        run: list[OutboxRow] = []
        for row in raw:
            candidate = self._to_row(row)
            if run and (
                candidate.sequence != run[-1].sequence + 1 or candidate.priority != run[0].priority
            ):
                break
            run.append(candidate)
        return run

    def batch(self, batch_id: str) -> BatchRow | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM batches WHERE batch_id = ?", (batch_id,)
            ).fetchone()
        return self._to_batch(row) if row else None

    def batches_ready(self, *, now_us: int | None = None, limit: int = 10) -> list[BatchRow]:
        """Batch yang menunggu dikirim atau dicoba ulang, urut prioritas."""
        now_us = now_us if now_us is not None else now_micros()
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM batches WHERE state IN (?, ?) "
                "AND (next_attempt_at IS NULL OR next_attempt_at <= ?) "
                "ORDER BY priority ASC, sequence_start ASC LIMIT ?",
                (BatchState.BUILT.value, BatchState.UPLOADING.value, now_us, limit),
            ).fetchall()
        return [self._to_batch(r) for r in rows]

    def batch_records(self, batch_id: str) -> list[TelemetryRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload FROM outbox WHERE batch_id = ? ORDER BY sequence ASC",
                (batch_id,),
            ).fetchall()
        return [TelemetryRecord.model_validate_json(r["payload"]) for r in rows]

    # -- siklus hidup batch --------------------------------------------------

    def create_batch(self, batch: BatchRow, sequences: list[int]) -> None:
        """Catat batch dan ikat baris outbox padanya, dalam satu transaksi."""
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO batches(batch_id, sequence_start, sequence_end, "
                "first_timestamp, last_timestamp, record_count, payload_checksum, "
                "schema_version, priority, state, attempts) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    batch.batch_id,
                    batch.sequence_start,
                    batch.sequence_end,
                    batch.first_timestamp,
                    batch.last_timestamp,
                    batch.record_count,
                    batch.payload_checksum,
                    batch.schema_version,
                    int(batch.priority),
                    batch.state.value,
                    batch.attempts,
                ),
            )
            self._conn.executemany(
                "UPDATE outbox SET state = ?, batch_id = ? WHERE sequence = ?",
                [(RecordState.BATCHED.value, batch.batch_id, s) for s in sequences],
            )

    def mark_attempt(
        self, batch_id: str, *, transport: str, next_attempt_at: int | None, error: str | None
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE batches SET attempts = attempts + 1, state = ?, transport = ?, "
                "next_attempt_at = ?, last_error = ? WHERE batch_id = ?",
                (BatchState.UPLOADING.value, transport, next_attempt_at, error, batch_id),
            )

    def mark_acked(self, batch_id: str, *, transport: str) -> None:
        """Tandai batch tersimpan di central. **Satu-satunya jalan keluar dari pending.**"""
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE batches SET state = ?, acked_at = ?, transport = ?, "
                "last_error = NULL WHERE batch_id = ?",
                (BatchState.ACKED.value, now_micros(), transport, batch_id),
            )
            self._conn.execute(
                "UPDATE outbox SET state = ? WHERE batch_id = ?",
                (RecordState.ACKED.value, batch_id),
            )

    def mark_failed(self, batch_id: str, *, error: str) -> None:
        """Karantina batch. **Datanya tidak dihapus.**

        Batch yang gagal permanen berhenti dicoba ulang supaya tidak menyumbat
        antrean, tetapi barisnya tetap ada di outbox. Operator bisa melihatnya
        dan memutuskan — dibuang diam-diam adalah kehilangan data yang tak
        terlihat.
        """
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE batches SET state = ?, last_error = ? WHERE batch_id = ?",
                (BatchState.FAILED.value, error, batch_id),
            )

    def release_batch(self, batch_id: str) -> None:
        """Kembalikan baris ke pending dan buang batch-nya.

        Dipakai saat pembentukan batch gagal di tengah jalan; datanya harus
        kembali bisa diambil batch berikutnya.
        """
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE outbox SET state = ?, batch_id = NULL WHERE batch_id = ?",
                (RecordState.PENDING.value, batch_id),
            )
            self._conn.execute("DELETE FROM batches WHERE batch_id = ?", (batch_id,))

    # -- retensi ------------------------------------------------------------

    def prune_acked(self, *, older_than_us: int) -> int:
        """Hapus baris yang sudah acked dan melewati masa tenggang.

        **Hanya baris yang sudah acked.** Baris pending tidak pernah dipangkas,
        berapa pun tekanan disk — kehilangan data yang belum tersetor adalah
        kegagalan yang justru ingin dicegah seluruh sistem ini.

        Masa tenggang dihitung dari **kapan ACK diterima**, bukan kapan data
        diukur. Perbedaannya besar: kapal yang offline 30 hari lalu menyetor
        seluruh backlog-nya sekaligus akan punya data berumur 30 hari yang baru
        saja di-ACK. Kalau tenggang dihitung dari waktu pengukuran, semuanya
        terhapus seketika dan jendela replay lokal — yang justru jadi alasan
        tenggang ini ada — hilang tepat saat paling dibutuhkan.
        """
        with self._lock, self._conn:
            cur = self._conn.execute(
                "DELETE FROM outbox WHERE state = ? AND batch_id IN "
                "(SELECT batch_id FROM batches WHERE state = ? AND acked_at < ?)",
                (RecordState.ACKED.value, BatchState.ACKED.value, older_than_us),
            )
            deleted = cur.rowcount
            self._conn.execute(
                "DELETE FROM batches WHERE state = ? AND batch_id NOT IN "
                "(SELECT DISTINCT batch_id FROM outbox WHERE batch_id IS NOT NULL)",
                (BatchState.ACKED.value,),
            )
        return int(deleted)

    def stats(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT state, COUNT(*) AS n FROM outbox GROUP BY state"
            ).fetchall()
            batches = self._conn.execute(
                "SELECT state, COUNT(*) AS n FROM batches GROUP BY state"
            ).fetchall()
        out = {f"records_{r['state']}": int(r["n"]) for r in rows}
        out.update({f"batches_{r['state']}": int(r["n"]) for r in batches})
        return out

    # -- helper -------------------------------------------------------------

    @staticmethod
    def _to_row(row: sqlite3.Row) -> OutboxRow:
        return OutboxRow(
            sequence=int(row["sequence"]),
            captured_at=int(row["captured_at"]),
            priority=SyncPriority(int(row["priority"])),
            record=TelemetryRecord.model_validate_json(row["payload"]),
            state=RecordState(row["state"]),
            batch_id=row["batch_id"],
        )

    @staticmethod
    def _to_batch(row: sqlite3.Row) -> BatchRow:
        return BatchRow(
            batch_id=row["batch_id"],
            sequence_start=int(row["sequence_start"]),
            sequence_end=int(row["sequence_end"]),
            first_timestamp=int(row["first_timestamp"]),
            last_timestamp=int(row["last_timestamp"]),
            record_count=int(row["record_count"]),
            payload_checksum=row["payload_checksum"],
            schema_version=row["schema_version"],
            priority=SyncPriority(int(row["priority"])),
            state=BatchState(row["state"]),
            attempts=int(row["attempts"]),
            transport=row["transport"],
            next_attempt_at=row["next_attempt_at"],
            acked_at=row["acked_at"],
            last_error=row["last_error"],
        )


class SqliteSequenceSource:
    """Sumber sequence yang bertahan melewati restart.

    Menggantikan `InMemorySequenceSource` dari Phase 2. Itu penting: counter
    in-memory mulai dari nol setiap kali proses dijalankan ulang, sehingga nomor
    terpakai ulang dan data lama bisa tertimpa di central.
    """

    def __init__(self, store: OutboxStore) -> None:
        self._store = store

    def allocate(self, count: int) -> int:
        return self._store.allocate_sequence(count)

    @property
    def current(self) -> int:
        return self._store.current_sequence


def checksum_of(payload: bytes) -> str:
    """Checksum yang dipakai di seluruh protokol sync."""
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def canonical_json(data: Any) -> bytes:
    """JSON yang stabil byte-nya, untuk checksum manifest."""
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
