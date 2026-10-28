"""Enkripsi simetris untuk rahasia yang harus bisa dibaca kembali.

Kata sandi operator tetap di-hash Argon2id: tidak ada alasan untuk membacanya
lagi. Rahasia kredensial kapal berbeda. Admin perlu mengunduh ulang skrip
pasang kapal yang sudah terdaftar, dan itu hanya mungkin bila rahasianya bisa
dikembalikan, jadi ia disimpan terenkripsi di samping hash-nya. Hash tetap
dipakai untuk memverifikasi login kapal; salinan terenkripsi hanya dibaca saat
admin meminta skrip pasang.

Kuncinya sengaja terpisah dari `jwt_secret`. Memutar `jwt_secret` adalah langkah
yang dianjurkan saat kapal dijual (docs/operations/08-ship-decommissioning.md);
kalau kunci ini diturunkan darinya, langkah itu diam-diam membuat seluruh
kredensial armada tidak terbaca.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from fleetview_common import ConfigError, ValidationError

__all__ = ["SecretBox"]


class SecretBox:
    def __init__(self, key: str | None) -> None:
        self._fernet: Fernet | None = None
        if not key:
            return
        try:
            self._fernet = Fernet(key.encode())
        except ValueError as exc:
            # Gagal saat start, bukan saat admin menekan tombol: kunci yang
            # salah ketik harus ketahuan di log deploy, bukan di tengah kerja.
            raise ConfigError(
                "credential_key bukan kunci Fernet yang valid (harus 32 byte base64 url-safe)",
                code="config.invalid_credential_key",
            ) from exc

    @property
    def enabled(self) -> bool:
        return self._fernet is not None

    def encrypt(self, plain: str) -> str:
        if self._fernet is None:
            raise ConfigError(
                "credential_key belum diisi, rahasia tidak bisa disimpan terenkripsi",
                code="config.credential_key_missing",
            )
        return self._fernet.encrypt(plain.encode()).decode()

    def decrypt(self, token: str) -> str:
        if self._fernet is None:
            raise _unreadable("credential_key belum diisi di server")
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken as exc:
            raise _unreadable("kunci enkripsi berbeda dari saat rahasia disimpan") from exc


def _unreadable(reason: str) -> ValidationError:
    return ValidationError(
        f"rahasia kredensial tidak bisa dibuka: {reason}. Terbitkan kredensial baru.",
        code="fleet.credential_unreadable",
    )
