"""OutboxStore — penyimpanan durable untuk data yang belum disetor.

API-nya sinkron. SQLite sendiri sinkron, dan operasi di sini berukuran
sub-milidetik; membungkusnya dengan aiosqlite hanya menambah dependency tanpa
manfaat nyata. Pemanggil dari konteks async memakai `asyncio.to_thread`.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from fleetview_common import get_logger, now_micros
from fleetview_contracts import SyncPriority, TelemetryRecord
from fleetview_edge.outbox.models import BatchRow, BatchState, OutboxChunk, RecordState
from fleetview_edge.outbox.schema import DDL, SCHEMA_VERSION

__all__ = ["OutboxStore", "SqliteSequenceSource"]

log = get_logger(__name__)

SEQUENCE_COUNTER = "sequence"

#: Payload disimpan ter-gzip **per chunk**, bukan per pembacaan.
#:
#: Ini diukur, bukan diperkirakan. Benchmark Phase 9 menunjukkan outbox JSON
#: polos memakan 352 byte/titik — 2,65 GB/hari untuk satu kapal berisi 100
#: sensor pada 1 Hz, yaitu **79 GB untuk 30 hari offline**. Itu lebih dari
#: separuh SSD 128 GB yang dianjurkan, sebelum menghitung InfluxDB dan sistem
#: operasi, padahal 30 hari offline justru kasus yang sistem ini ada untuk
#: menanganinya.
#:
#: Gzip per pembacaan hampir tidak menolong: pengukuran memberi 220 byte/titik,
#: hanya 1,6x. Sebabnya, sebuah pembacaan JSON hanya ~350 byte sementara gzip
#: butuh jendela jauh lebih besar untuk menemukan pengulangan. Redundansi
#: telemetry ada **antar** pembacaan, bukan di dalam satu pembacaan: ship_id dan
#: device_id identik di setiap baris, nama kunci JSON berulang, dan nilai sensor
#: bertetangga hampir sama.
#:
#: Mengompresi satu chunk (~100 pembacaan) sekaligus memberi 14,4 byte/titik —
#: **24x**, atau 3,7 GB untuk 30 hari. Itulah alasan satu baris outbox menyimpan
#: sekelompok pembacaan, bukan satu.
#:
#: Level 6 (default) dipilih ketimbang level maksimum: selisih ukurannya kecil
#: untuk JSON telemetry sementara biaya CPU-nya beberapa kali lipat, dan
#: kompresi ini berada di jalur tulis yang dilewati setiap pembacaan.
_COMPRESS_LEVEL = 6

#: Batas atas jumlah pembacaan dalam satu chunk.
#:
#: Chunk adalah satuan state, jadi ukurannya menjadi granularitas terkecil
#: sebuah batch: `Batcher.max_records` tidak bisa memecah lebih halus dari ini.
#: Satu putaran polling normal jauh di bawah batas ini, jadi batas ini hanya
#: berlaku pada kasus tidak wajar — misalnya satu perangkat dengan ribuan
#: sensor, atau backfill — supaya satu chunk raksasa tidak memaksa satu batch
#: raksasa lewat jalur seluler yang sempit.
#:
#: 500 disamakan dengan default `Batcher.max_records`. Manfaat kompresi sudah
#: jenuh jauh sebelum angka ini, jadi membatasinya tidak menaikkan kembali
#: ukuran penyimpanan.
_MAX_CHUNK_RECORDS = 500


def _pack(records: list[TelemetryRecord]) -> bytes:
    body = json.dumps([r.model_dump(mode="json") for r in records], separators=(",", ":"))
    # mtime=0 supaya byte-nya deterministik.
    return gzip.compress(body.encode("utf-8"), _COMPRESS_LEVEL, mtime=0)


def _unpack(payload: bytes) -> list[TelemetryRecord]:
    return [TelemetryRecord.model_validate(d) for d in json.loads(gzip.decompress(payload))]


def _contiguous_groups(records: list[TelemetryRecord]) -> list[list[TelemetryRecord]]:
    """Pecah record menjadi kelompok yang nomor sequence-nya benar-benar runut.

    Satu panggilan append() hampir selalu runut, tetapi celah bisa muncul kalau
    proses mati antara alokasi sequence dan penyimpanan. Sebuah chunk menjanjikan
    `record_count == sequence_end - sequence_start + 1`; menyimpan celah di dalam
    chunk akan mematahkan janji itu dan membuat envelope batch tidak valid.
    """
    ordered = sorted(records, key=lambda r: r.sequence_number)
    groups: list[list[TelemetryRecord]] = []
    for record in ordered:
        runs_on = (
            groups
            and record.sequence_number == groups[-1][-1].sequence_number + 1
            and len(groups[-1]) < _MAX_CHUNK_RECORDS
        )
        if runs_on:
            groups[-1].append(record)
        else:
            groups.append([record])
    return groups


def _migrate(conn: sqlite3.Connection) -> None:
    """Naikkan database v1 (satu baris per pembacaan) ke v2 (satu baris per chunk).

    Dijalankan sebelum DDL, karena `CREATE TABLE IF NOT EXISTS` tidak akan
    menyentuh tabel v1 yang sudah ada dan setiap query v2 setelahnya akan gagal
    pada kolom yang tidak ada.

    Migrasi ini membaca ulang setiap baris lama dan menuliskannya kembali
    sebagai chunk, dalam satu transaksi. Prinsip yang berlaku sama seperti di
    tempat lain: data lokal tidak boleh hilang — termasuk tidak boleh hilang
    karena agent-nya di-upgrade.
    """
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "outbox" not in tables:
        return  # database baru; DDL akan membuat skema v2 langsung
    columns = {r[1] for r in conn.execute("PRAGMA table_info(outbox)")}
    if "sequence_start" in columns:
        return  # sudah v2

    rows = conn.execute(
        "SELECT sequence, captured_at, priority, payload, state, batch_id "
        "FROM outbox ORDER BY sequence ASC"
    ).fetchall()
    log.info("outbox.migrating", from_version=1, to_version=SCHEMA_VERSION, records=len(rows))

    # Kelompokkan baris yang runut DAN berbagi state, prioritas, serta batch
    # yang sama. State ikut jadi pembatas: setelah migrasi state melekat pada
    # chunk, jadi mencampur baris acked dan pending dalam satu chunk akan
    # menghilangkan atau mengirim ulang salah satunya.
    groups: list[list[Any]] = []
    for row in rows:
        prev = groups[-1][-1] if groups else None
        same_run = (
            prev is not None
            and row["sequence"] == prev["sequence"] + 1
            and row["state"] == prev["state"]
            and row["priority"] == prev["priority"]
            and row["batch_id"] == prev["batch_id"]
        )
        if same_run:
            groups[-1].append(row)
        else:
            groups.append([row])

    def _payload_of(raw: bytes | str) -> dict[str, Any]:
        if isinstance(raw, bytes) and raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        data: dict[str, Any] = json.loads(raw)
        return data

    packed = [
        (
            g[0]["sequence"],
            g[-1]["sequence"],
            len(g),
            min(int(r["captured_at"]) for r in g),
            int(g[0]["priority"]),
            gzip.compress(
                json.dumps([_payload_of(r["payload"]) for r in g], separators=(",", ":")).encode(),
                _COMPRESS_LEVEL,
                mtime=0,
            ),
            g[0]["state"],
            g[0]["batch_id"],
        )
        for g in groups
    ]

    with conn:
        conn.execute("ALTER TABLE outbox RENAME TO outbox_v1")
        conn.execute("DROP INDEX IF EXISTS ix_outbox_pending")
        conn.execute("DROP INDEX IF EXISTS ix_outbox_batch")
        conn.executescript(DDL)
        conn.executemany(
            "INSERT INTO outbox(sequence_start, sequence_end, record_count, captured_at, "
            "priority, payload, state, batch_id) VALUES(?,?,?,?,?,?,?,?)",
            packed,
        )
        conn.execute("DROP TABLE outbox_v1")
    log.info("outbox.migrated", records=len(rows), chunks=len(packed))


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
        _migrate(self._conn)
        self._conn.executescript(DDL)
        self._conn.execute(
            "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(SCHEMA_VERSION),),
        )
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
        groups = _contiguous_groups(records)
        rows = [
            (
                g[0].sequence_number,
                g[-1].sequence_number,
                len(g),
                min(r.timestamp for r in g),
                int(priority if priority is not None else SyncPriority.RAW),
                _pack(g),
                RecordState.PENDING.value,
            )
            for g in groups
        ]
        with self._lock, self._conn:
            self._conn.executemany(
                "INSERT OR IGNORE INTO outbox(sequence_start, sequence_end, record_count, "
                "captured_at, priority, payload, state) VALUES(?,?,?,?,?,?,?)",
                rows,
            )
        return sum(len(g) for g in groups)

    # -- pembacaan ----------------------------------------------------------

    def pending_count(self, *, max_priority: SyncPriority | None = None) -> int:
        sql = "SELECT COALESCE(SUM(record_count), 0) AS n FROM outbox WHERE state = ?"
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
    ) -> list[OutboxChunk]:
        """Ambil chunk pending yang **sequence-nya bersambung**, sampai ~`limit` record.

        Kerunutan itu wajib, bukan optimasi: envelope batch menuntut
        `record_count == sequence_end - sequence_start + 1`. Celah — yang bisa
        muncul kalau proses mati antara alokasi sequence dan penyimpanan —
        diperlakukan sebagai batas batch, jadi batch berikutnya mulai setelahnya.

        Semua chunk yang dikembalikan berbagi prioritas yang sama, sehingga
        pengiriman berdasarkan prioritas benar-benar berlaku per batch.

        `limit` adalah batas jumlah **record**, dan dihormati pada batas chunk.
        Satu chunk selalu dikembalikan utuh meski sendirian sudah melebihi
        `limit`: memotongnya akan menandai seluruh chunk terkirim padahal
        sebagiannya tidak. Chunk berukuran satu putaran polling, jadi selisihnya
        kecil.
        """
        sql = (
            "SELECT sequence_start, sequence_end, record_count, captured_at, priority, "
            "payload, state, batch_id FROM outbox WHERE state = ?"
        )
        params: list[Any] = [RecordState.PENDING.value]
        if max_priority is not None:
            sql += " AND priority <= ?"
            params.append(int(max_priority))
        sql += " ORDER BY priority ASC, sequence_start ASC LIMIT ?"
        # Batas baris yang murah hati: chunk jauh lebih besar dari satu record,
        # jadi sedikit baris sudah cukup untuk memenuhi limit record.
        params.append(max(1, limit))

        with self._lock:
            raw = self._conn.execute(sql, params).fetchall()

        run: list[OutboxChunk] = []
        total = 0
        for row in raw:
            candidate = self._to_chunk(row)
            if run:
                if candidate.sequence_start != run[-1].sequence_end + 1:
                    break
                if candidate.priority != run[0].priority:
                    break
                if total + candidate.record_count > limit:
                    break
            run.append(candidate)
            total += candidate.record_count
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
                "SELECT payload FROM outbox WHERE batch_id = ? ORDER BY sequence_start ASC",
                (batch_id,),
            ).fetchall()
        return [record for r in rows for record in _unpack(r["payload"])]

    # -- siklus hidup batch --------------------------------------------------

    def create_batch(self, batch: BatchRow, chunk_starts: list[int]) -> None:
        """Catat batch dan ikat chunk outbox padanya, dalam satu transaksi.

        `chunk_starts` adalah `sequence_start` tiap chunk — chunk selalu diikat
        utuh; lihat `claim_contiguous_run`.
        """
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
                "UPDATE outbox SET state = ?, batch_id = ? WHERE sequence_start = ?",
                [(RecordState.BATCHED.value, batch.batch_id, s) for s in chunk_starts],
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
        doomed = (
            "SELECT batch_id FROM batches WHERE state = ? AND acked_at < ?",
            (BatchState.ACKED.value, older_than_us),
        )
        with self._lock, self._conn:
            # Dihitung sebelum dihapus, dan dalam satuan record, bukan baris:
            # satu baris outbox kini berisi banyak pembacaan, jadi rowcount akan
            # melaporkan angka yang jauh lebih kecil dari kenyataan.
            row = self._conn.execute(
                f"SELECT COALESCE(SUM(record_count), 0) AS n FROM outbox "
                f"WHERE state = ? AND batch_id IN ({doomed[0]})",
                (RecordState.ACKED.value, *doomed[1]),
            ).fetchone()
            deleted = int(row["n"])
            self._conn.execute(
                f"DELETE FROM outbox WHERE state = ? AND batch_id IN ({doomed[0]})",
                (RecordState.ACKED.value, *doomed[1]),
            )
            self._conn.execute(
                "DELETE FROM batches WHERE state = ? AND batch_id NOT IN "
                "(SELECT DISTINCT batch_id FROM outbox WHERE batch_id IS NOT NULL)",
                (BatchState.ACKED.value,),
            )
        return deleted

    def stats(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT state, SUM(record_count) AS n FROM outbox GROUP BY state"
            ).fetchall()
            batches = self._conn.execute(
                "SELECT state, COUNT(*) AS n FROM batches GROUP BY state"
            ).fetchall()
        out = {f"records_{r['state']}": int(r["n"]) for r in rows}
        out.update({f"batches_{r['state']}": int(r["n"]) for r in batches})
        return out

    # -- helper -------------------------------------------------------------

    @staticmethod
    def _to_chunk(row: sqlite3.Row) -> OutboxChunk:
        return OutboxChunk(
            sequence_start=int(row["sequence_start"]),
            sequence_end=int(row["sequence_end"]),
            captured_at=int(row["captured_at"]),
            priority=SyncPriority(int(row["priority"])),
            records=_unpack(row["payload"]),
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
