"""Taksonomi error bersama.

Yang membuat taksonomi ini layak ada adalah flag `retryable`. Protokol sync
memperlakukan pembedaan "boleh diulang" versus "jangan diulang" secara harfiah:
batch yang rusak permanen dikarantina dan dilaporkan, sementara kegagalan
sementara diulang dengan backoff. Kalau setiap modul menebak sendiri, edge bisa
mengulang selamanya sesuatu yang tidak akan pernah berhasil — atau lebih buruk,
membuang data yang sebenarnya masih bisa dikirim.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "AuthError",
    "ConfigError",
    "FleetViewError",
    "ProtocolError",
    "StorageError",
    "TransportError",
    "ValidationError",
]


class FleetViewError(Exception):
    """Akar semua error yang ditangani sistem.

    Atribut:
        code: pengenal stabil dan mesin-terbaca, mis. `storage.outbox_full`.
              Muncul di log dan di response API; jangan diubah sembarangan.
        message: penjelasan untuk manusia.
        details: konteks tambahan. **Tidak boleh berisi secret** — ini ikut
                 tercetak di log dan bisa terkirim ke client.
        retryable: apakah mengulang operasi yang sama masih masuk akal.
    """

    code: str = "internal.unknown"
    retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        self.details = details or {}
        if retryable is not None:
            self.retryable = retryable

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
            "retryable": self.retryable,
        }

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r}, message={self.message!r})"


class ConfigError(FleetViewError):
    """Konfigurasi hilang, salah bentuk, atau saling bertentangan.

    Tidak pernah retryable: mengulang tidak akan memperbaiki file config.
    """

    code = "config.invalid"
    retryable = False


class ValidationError(FleetViewError):
    """Data masuk gagal validasi. Tidak retryable — datanya sendiri yang salah."""

    code = "validation.failed"
    retryable = False


class ProtocolError(FleetViewError):
    """Kegagalan saat berbicara dengan perangkat lapangan.

    Retryable secara default: kabel longgar, timeout, dan gangguan bus adalah
    kondisi sementara yang lazim di kapal.
    """

    code = "protocol.failure"
    retryable = True


class StorageError(FleetViewError):
    """Kegagalan pada penyimpanan lokal atau pusat (SQLite, InfluxDB, Postgres)."""

    code = "storage.failure"
    retryable = True


class TransportError(FleetViewError):
    """Kegagalan jaringan saat sinkronisasi."""

    code = "transport.failure"
    retryable = True


class AuthError(FleetViewError):
    """Autentikasi atau otorisasi gagal.

    Tidak retryable: kredensial yang ditolak tidak akan diterima pada percobaan
    berikutnya, dan mengulang hanya memicu rate limit.
    """

    code = "auth.denied"
    retryable = False
