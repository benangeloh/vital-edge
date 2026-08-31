"""Test model bidang ingest."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from fleetview_contracts import SyncState, UploadSession


def test_has_gap_true_saat_highest_melebihi_contiguous() -> None:
    """Celah wajar terjadi setelah impor USB berisi data lama mendarat lebih
    dulu daripada batch online yang sedang dalam perjalanan."""
    state = SyncState(
        ship_id=uuid4(),
        last_contiguous_sequence=1000,
        highest_sequence_seen=1500,
        server_time=datetime.now(UTC),
    )
    assert state.has_gap is True


def test_has_gap_false_saat_berurutan() -> None:
    state = SyncState(
        ship_id=uuid4(),
        last_contiguous_sequence=1500,
        highest_sequence_seen=1500,
        server_time=datetime.now(UTC),
    )
    assert state.has_gap is False


def test_missing_chunks_hanya_mengembalikan_yang_belum_diterima() -> None:
    """Inilah inti kemampuan resume: setelah putus, edge hanya mengirim sisanya."""
    session = UploadSession(
        session_id=uuid4(),
        batch_id=uuid4(),
        chunk_size=1024,
        total_chunks=5,
        received_chunks=[0, 1, 3],
        expires_at=datetime.now(UTC),
    )
    assert session.missing_chunks == [2, 4]


def test_missing_chunks_kosong_saat_semua_sudah_masuk() -> None:
    session = UploadSession(
        session_id=uuid4(),
        batch_id=uuid4(),
        chunk_size=1024,
        total_chunks=3,
        received_chunks=[0, 1, 2],
        expires_at=datetime.now(UTC),
    )
    assert session.missing_chunks == []
