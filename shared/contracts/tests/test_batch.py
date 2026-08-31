"""Test BatchEnvelope.

Invariant di sini adalah yang membuat sync bisa dipercaya: rentang sequence yang
berurutan tanpa celah, checksum yang berbentuk benar, dan timestamp yang selalu
membawa timezone.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from fleetview_contracts import BatchEnvelope

_CHECKSUM = "sha256:" + "a" * 64


def _envelope(**overrides: object) -> BatchEnvelope:
    now = datetime.now(UTC)
    base: dict[str, object] = {
        "batch_id": uuid4(),
        "ship_id": uuid4(),
        "device_id": uuid4(),
        "sequence_start": 1000,
        "sequence_end": 1499,
        "first_timestamp": now,
        "last_timestamp": now,
        "record_count": 500,
        "payload_checksum": _CHECKSUM,
        "payload_size": 12345,
        "agent_version": "0.1.0",
        "config_version": "cfg-1",
        "created_at": now,
    }
    return BatchEnvelope(**{**base, **overrides})


def test_envelope_valid_bisa_dibuat() -> None:
    env = _envelope()
    assert env.record_count == 500
    assert env.sequence_end - env.sequence_start + 1 == env.record_count


def test_record_count_harus_cocok_dengan_rentang_sequence() -> None:
    """Ketidakcocokan berarti ada celah di outbox — itu bug, bukan kondisi wajar."""
    with pytest.raises(ValidationError, match="tidak cocok dengan rentang"):
        _envelope(record_count=499)


def test_sequence_end_tidak_boleh_mendahului_start() -> None:
    with pytest.raises(ValidationError):
        _envelope(sequence_start=1500, sequence_end=1000, record_count=1)


def test_checksum_harus_berbentuk_sha256_heksadesimal() -> None:
    with pytest.raises(ValidationError, match="payload_checksum"):
        _envelope(payload_checksum="md5:abc")


def test_timestamp_naif_ditolak() -> None:
    """Jam kapal melenceng dan melompat. Timestamp tanpa zona waktu membuat
    kondisi itu mustahil dipahami setelah kejadian."""
    with pytest.raises(ValidationError, match="timezone"):
        _envelope(created_at=datetime(2026, 1, 1, 0, 0, 0))  # noqa: DTZ001


def test_last_timestamp_tidak_boleh_mendahului_first() -> None:
    now = datetime.now(UTC)
    earlier = datetime(2020, 1, 1, tzinfo=UTC)
    with pytest.raises(ValidationError, match="lebih awal"):
        _envelope(first_timestamp=now, last_timestamp=earlier)
