"""Redaksi konfigurasi dan pembatasan akses Console.

Console berjalan di kapal dan dibuka teknisi. Halaman konfigurasi harus cukup
berguna untuk troubleshooting, tetapi **tidak boleh menampilkan rahasia**.

Prinsipnya: redaksi berdasarkan **daftar pola nama**, bukan daftar kunci yang
diizinkan. Config bertambah seiring waktu, dan kunci baru yang berisi rahasia
akan lolos dari daftar izin — sementara pola nama menangkapnya secara default.
Salah di sisi menyembunyikan lebih baik daripada salah di sisi membocorkan.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = ["REDACTED", "is_sensitive_key", "redact_config"]

REDACTED = "••••••••"
NOT_SET = "(belum diisi)"

#: Nilai yang sudah merupakan hasil redaksi. Redaksi diterapkan dua kali secara
#: sengaja — sekali oleh penyedia config, sekali lagi oleh Console — sehingga
#: fungsinya HARUS idempoten. Tanpa ini, lapis kedua mengubah penanda
#: "belum diisi" menjadi "tersembunyi", dan teknisi kehilangan informasi
#: troubleshooting yang justru dibutuhkan: apakah tokennya lupa diisi.
_ALREADY_REDACTED = frozenset({REDACTED, NOT_SET})

_SENSITIVE = re.compile(
    r"(token|secret|password|passwd|credential|api_?key|private_?key|"
    r"auth|signature|salt|dsn)",
    re.IGNORECASE,
)


def is_sensitive_key(key: str) -> bool:
    return bool(_SENSITIVE.search(key))


def redact_config(data: Any) -> Any:
    """Kembalikan salinan config dengan nilai sensitif diganti penanda.

    Rekursif ke dalam dict dan list. Nilai kosong tetap ditampilkan apa adanya —
    membedakan "rahasia tersembunyi" dari "rahasia belum diisi" adalah informasi
    troubleshooting yang penting, dan tidak membocorkan apa pun.
    """
    if isinstance(data, dict):
        out: dict[str, Any] = {}
        for key, value in data.items():
            if is_sensitive_key(str(key)):
                if value in _ALREADY_REDACTED:
                    out[key] = value  # idempoten: jangan redaksi ulang penanda
                elif value in (None, ""):
                    out[key] = NOT_SET
                else:
                    out[key] = REDACTED
            else:
                out[key] = redact_config(value)
        return out
    if isinstance(data, list):
        return [redact_config(item) for item in data]
    return data
