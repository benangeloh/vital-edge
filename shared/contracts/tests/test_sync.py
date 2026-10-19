"""Test model bidang ingest."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import ClassVar
from uuid import uuid4

import pytest

from fleetview_contracts import EdgeHealth, SyncState, UploadSession


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


class TestEdgeHealthOpsional:
    """CPU dan memori tidak terbaca di non-Linux; itu bukan nol, itu tidak diketahui."""

    _BASE: ClassVar[dict[str, object]] = {
        "disk_used_percent": 60.0,
        "uptime_seconds": 10,
        "collector_healthy": True,
        "storage_healthy": True,
        "protocol_link_healthy": True,
    }

    def test_cpu_dan_memori_boleh_tidak_ada(self) -> None:
        health = EdgeHealth(**self._BASE)
        assert health.cpu_used_percent is None
        assert health.memory_used_percent is None

    def test_cpu_dan_memori_tetap_diterima_bila_ada(self) -> None:
        health = EdgeHealth(**self._BASE, cpu_used_percent=12.5, memory_used_percent=40.0)
        assert (health.cpu_used_percent, health.memory_used_percent) == (12.5, 40.0)

    @pytest.mark.parametrize("field", ["cpu_used_percent", "memory_used_percent"])
    @pytest.mark.parametrize("bad", [-0.1, 100.1])
    def test_rentang_tetap_dijaga(self, field: str, bad: float) -> None:
        with pytest.raises(ValueError, match=field):
            EdgeHealth(**self._BASE, **{field: bad})

    def test_uptime_boleh_tidak_ada(self) -> None:
        """Uptime 0 terlihat seperti perangkat baru restart; tidak diketahui itu lain."""
        base = {k: v for k, v in self._BASE.items() if k != "uptime_seconds"}
        assert EdgeHealth(**base).uptime_seconds is None

    def test_disk_tetap_wajib(self) -> None:
        base = {k: v for k, v in self._BASE.items() if k != "disk_used_percent"}
        with pytest.raises(ValueError, match="disk_used_percent"):
            EdgeHealth(**base)
