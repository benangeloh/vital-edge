"""Skema SQLite outbox.

Outbox adalah **source of truth untuk data yang belum berhasil disetor**.
Sekali sebuah pembacaan commit di sini, ia selamat dari mati listrik, dari
InfluxDB yang mati, dan dari kondisi jaringan apa pun.

Kenapa SQLite dan bukan InfluxDB: outbox butuh sequence monoton yang bertahan
melewati restart, dan state per batch yang bisa berubah
(`pending -> batched -> acked`). InfluxDB tidak menyediakan satu pun dari itu.
SQLite di sini **tidak menyimpan telemetry yang bisa di-query** — ia menyimpan
pembukuan dan payload buram yang menunggu ACK. Lihat
docs/architecture/00-assessment.md keputusan D3.
"""

from __future__ import annotations

from typing import Final

SCHEMA_VERSION: Final[int] = 2

DDL: Final[str] = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Counter sequence. Bertahan melewati restart; inilah yang membuat nomor
-- sequence tidak terpakai ulang setelah kapal mati listrik.
CREATE TABLE IF NOT EXISTS counters (
    name  TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS batches (
    batch_id         TEXT PRIMARY KEY,
    sequence_start   INTEGER NOT NULL,
    sequence_end     INTEGER NOT NULL,
    first_timestamp  INTEGER NOT NULL,
    last_timestamp   INTEGER NOT NULL,
    record_count     INTEGER NOT NULL,
    payload_checksum TEXT    NOT NULL,
    schema_version   TEXT    NOT NULL,
    priority         INTEGER NOT NULL,
    state            TEXT    NOT NULL,
    attempts         INTEGER NOT NULL DEFAULT 0,
    transport        TEXT,
    next_attempt_at  INTEGER,
    acked_at         INTEGER,
    last_error       TEXT
);

CREATE INDEX IF NOT EXISTS ix_batches_state
    ON batches(state, priority, next_attempt_at);

CREATE TABLE IF NOT EXISTS outbox (
    -- Satu baris = satu chunk, yaitu satu kali panggilan append() dari
    -- collector (umumnya satu putaran polling, ~100 pembacaan). BUKAN satu
    -- baris per pembacaan. Lihat store.py untuk alasan terukurnya.
    sequence_start INTEGER PRIMARY KEY,
    sequence_end   INTEGER NOT NULL,
    record_count   INTEGER NOT NULL,
    captured_at    INTEGER NOT NULL,   -- timestamp tertua di dalam chunk
    priority       INTEGER NOT NULL,
    payload        BLOB    NOT NULL,   -- gzip(JSON array of TelemetryRecord)
    state          TEXT    NOT NULL,
    batch_id       TEXT REFERENCES batches(batch_id) ON DELETE SET NULL
);

-- Jalur query terpanas: "chunk pending berikutnya menurut prioritas".
CREATE INDEX IF NOT EXISTS ix_outbox_pending
    ON outbox(state, priority, sequence_start);

CREATE INDEX IF NOT EXISTS ix_outbox_batch
    ON outbox(batch_id);
"""
