"""Model bidang ingest: ledger batch, sesi upload, dan watermark per kapal."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID  # noqa: N811
from sqlalchemy.orm import Mapped, mapped_column

from fleetview_central.platform.db import Base

__all__ = ["ShipSyncState", "SyncBatch", "UploadChunk", "UploadSession"]


class SyncBatch(Base):
    """Ledger idempotensi sekaligus jejak audit.

    `batch_id` dibuat di kapal dan menjadi primary key di sini. Karena itu
    pengiriman ulang menghasilkan konflik primary key — bukan data ganda. Itu
    lapis pertama pengaman duplikat; lapis kedua ada di InfluxDB, di mana
    identitas titik adalah measurement + tag + timestamp.
    """

    __tablename__ = "sync_batches"
    __table_args__ = (
        UniqueConstraint("ship_id", "sequence_start", "sequence_end", name="uq_batch_range"),
    )

    batch_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    ship_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), index=True)
    device_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), index=True)

    sequence_start: Mapped[int] = mapped_column(BigInteger)
    sequence_end: Mapped[int] = mapped_column(BigInteger)
    first_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    record_count: Mapped[int] = mapped_column(Integer)

    payload_checksum: Mapped[str] = mapped_column(String(80))
    payload_encoding: Mapped[str] = mapped_column(String(32))
    payload_size: Mapped[int] = mapped_column(Integer)
    schema_version: Mapped[str] = mapped_column(String(16))

    agent_version: Mapped[str | None] = mapped_column(String(32))
    config_version: Mapped[str | None] = mapped_column(String(64))
    transport: Mapped[str | None] = mapped_column(String(16))

    status: Mapped[str] = mapped_column(String(16), default="staging", index=True)
    """staging | committed | rejected."""

    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reject_reason: Mapped[str | None] = mapped_column(Text)

    ack_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    """ACK yang diterbitkan, disimpan apa adanya.

    Kalau ACK hilang di jalan, kapal mengirim ulang batch yang sama dan kita
    memutar ulang ACK ini — byte-nya identik. Itulah yang membuat ACK hilang
    berbiaya satu round trip, bukan satu upload ulang.
    """


class UploadSession(Base):
    """Sesi upload berpotongan yang bisa dilanjutkan."""

    __tablename__ = "upload_sessions"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    batch_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), unique=True, index=True)
    ship_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), index=True)
    envelope: Mapped[dict[str, Any]] = mapped_column(JSON)
    chunk_size: Mapped[int] = mapped_column(Integer)
    total_chunks: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class UploadChunk(Base):
    """Satu potongan yang sudah diterima.

    Disimpan di Postgres, bukan di filesystem, supaya kemajuan upload ikut dalam
    transaksi yang sama dengan ledger — tidak ada berkas yatim yang tertinggal
    kalau proses mati di tengah.
    """

    __tablename__ = "upload_chunks"
    __table_args__ = (UniqueConstraint("session_id", "chunk_index", name="uq_session_chunk"),)

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("upload_sessions.id", ondelete="CASCADE"), index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ShipSyncState(Base):
    """Satu baris per kapal. Sumber data tampilan armada.

    Didenormalisasi oleh jalur ingest supaya tampilan 70 kapal hanya butuh satu
    query ber-index, berapa pun besar volume telemetry-nya.
    """

    __tablename__ = "ship_sync_state"

    ship_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("ships.id", ondelete="CASCADE"), primary_key=True
    )
    last_contiguous_sequence: Mapped[int] = mapped_column(BigInteger, default=0)
    """Watermark resmi: sampai sequence berapa data lengkap tanpa celah."""
    highest_sequence_seen: Mapped[int] = mapped_column(BigInteger, default=0)
    """Bila lebih besar dari watermark, ada celah — normal setelah impor USB
    berisi data lama mendarat lebih dulu daripada batch online."""

    last_batch_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_telemetry_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    pending_estimate: Mapped[int | None] = mapped_column(BigInteger)
    oldest_pending_age_seconds: Mapped[int | None] = mapped_column(BigInteger)
    connection_state: Mapped[str] = mapped_column(String(16), default="offline")
    active_transport: Mapped[str | None] = mapped_column(String(16))
    edge_health: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    config_version: Mapped[str | None] = mapped_column(String(64))
    agent_version: Mapped[str | None] = mapped_column(String(32))
    total_batches: Mapped[int] = mapped_column(BigInteger, default=0)
    total_records: Mapped[int] = mapped_column(BigInteger, default=0)
