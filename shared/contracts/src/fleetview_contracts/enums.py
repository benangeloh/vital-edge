"""Nilai enumerasi bersama.

Sengaja dibuat `str`-enum agar serialisasi JSON-nya terbaca manusia — nilai-nilai
ini muncul di log, di payload batch, dan sebagai tag InfluxDB, jadi keterbacaannya
lebih berharga daripada kepadatan byte.
"""

from enum import StrEnum

__all__ = [
    "KNOWN_MEASUREMENTS",
    "AcquisitionSource",
    "BatchStatus",
    "ConnectionState",
    "Quality",
    "Transport",
]


class Quality(StrEnum):
    """Tingkat kepercayaan terhadap satu pembacaan.

    Pembacaan yang buruk tetap dikirim disertai flag, bukan dibuang — supaya
    central bisa membedakan "sensornya rusak" dari "kita tidak punya datanya".
    """

    GOOD = "good"
    STALE = "stale"
    """Sensor tidak memperbarui nilainya dalam batas waktu yang ditetapkan."""
    SUSPECT = "suspect"
    """Nilai lolos parsing tapi gagal uji kewajaran (range / laju perubahan)."""
    SUBSTITUTED = "substituted"
    """Nilai berasal dari fallback (nilai terakhir, default), bukan dari sensor."""


class AcquisitionSource(StrEnum):
    """Cara sebuah pembacaan sampai ke Edge Agent.

    Ini ada karena jalur integrasi LP-A104 belum terkonfirmasi: sebagian kandidat
    menghasilkan aliran real-time, satu kandidat menghasilkan impor file secara
    berkala. Keduanya harus bisa dibedakan di hilir, karena impor file wajar
    membawa timestamp lama dan urutan yang tidak rapi.

    Lihat docs/hardware/LP-A104.md.
    """

    LIVE = "live"
    """Di-polling langsung dari perangkat lapangan."""
    FILE_IMPORT = "file_import"
    """Diurai dari file log yang diproduksi perangkat lapangan."""
    SIMULATED = "simulated"
    """Dihasilkan SimulatorAdapter. Tidak boleh muncul di produksi."""
    MANUAL = "manual"
    """Dimasukkan operator lewat Edge Console."""


class Transport(StrEnum):
    """Jalur fisik yang dipakai satu batch untuk sampai ke central."""

    LAN = "lan"
    WIFI = "wifi"
    CELLULAR = "cellular"
    USB = "usb"


class BatchStatus(StrEnum):
    """Status batch di ledger server."""

    STAGING = "staging"
    COMMITTED = "committed"
    REJECTED = "rejected"


class ConnectionState(StrEnum):
    """Bagaimana central memandang sebuah kapal."""

    ONLINE = "online"
    DEGRADED = "degraded"
    OFFLINE = "offline"


KNOWN_MEASUREMENTS: frozenset[str] = frozenset(
    {
        "rpm",
        "fuel_level",
        "fuel_flow",
        "pressure",
        "temperature",
        "voltage",
        "engine_load",
        "position",
        "_unknown",
    }
)
"""Measurement yang sudah dikenal saat ini.

Ini **acuan, bukan pembatas**. `Reading.measurement` menerima slug apa pun yang
valid, karena persyaratan menyebut sensor bisa bertambah di masa depan tanpa
perubahan kode. Nilai di luar daftar ini sah — hanya belum punya dashboard.
"""
