"""Test taksonomi error.

Flag `retryable` adalah alasan taksonomi ini ada — Sync Engine dan lapisan HTTP
sama-sama mematuhinya, jadi default-nya harus benar dan tidak berubah diam-diam.
"""

from __future__ import annotations

import pytest

from fleetview_common import (
    AuthError,
    ConfigError,
    FleetViewError,
    ProtocolError,
    StorageError,
    TransportError,
    ValidationError,
)


@pytest.mark.parametrize(
    ("exc_type", "expected"),
    [
        (ConfigError, False),  # mengulang tidak memperbaiki berkas config
        (ValidationError, False),  # datanya sendiri yang salah
        (AuthError, False),  # kredensial ditolak tetap ditolak
        (ProtocolError, True),  # kabel longgar / timeout itu sementara
        (StorageError, True),
        (TransportError, True),
    ],
)
def test_default_retryable_per_jenis_error(exc_type: type[FleetViewError], expected: bool) -> None:
    assert exc_type("pesan").retryable is expected


def test_retryable_bisa_ditimpa_per_kejadian() -> None:
    """Sebagian kegagalan storage memang fatal, mis. disk rusak."""
    assert StorageError("disk rusak", retryable=False).retryable is False


def test_to_dict_membawa_seluruh_bidang() -> None:
    exc = StorageError("outbox penuh", code="storage.outbox_full", details={"used": 99})
    assert exc.to_dict() == {
        "code": "storage.outbox_full",
        "message": "outbox penuh",
        "details": {"used": 99},
        "retryable": True,
    }


def test_semua_error_mewarisi_akar_yang_sama() -> None:
    """Satu klausa except di batas sistem harus bisa menangkap semuanya."""
    for exc_type in (
        ConfigError,
        ValidationError,
        ProtocolError,
        StorageError,
        TransportError,
        AuthError,
    ):
        assert issubclass(exc_type, FleetViewError)


def test_code_default_stabil() -> None:
    """Nilai code ikut ke log dan response API; jangan berubah diam-diam."""
    assert ProtocolError("x").code == "protocol.failure"
    assert AuthError("x").code == "auth.denied"
