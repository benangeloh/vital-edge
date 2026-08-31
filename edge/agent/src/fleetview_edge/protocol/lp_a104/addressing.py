"""Model alamat device LP-A Series — LANGSUNG DARI MANUAL.

Sumber: `docs/hardware/MTL-LPAU1-V3.3-EN_20260325_W.pdf`
        §9.1.2 Device Range, §9.1.2.3 Word/Bit address inclusion, §9.2 CAN Memory Mapping

**Semua angka di berkas ini disalin dari manual, bukan ditebak.** Setiap konstanta
mencantumkan bagian manualnya. Rumus CAN sudah diverifikasi terhadap beberapa baris
tabel yang tercetak (IN 0–3, OUT(command) 23, OUT(command) 60).

Yang TIDAK ada di berkas ini, karena tidak terdokumentasi di manual mana pun:
protokol transport untuk benar-benar membaca alamat-alamat ini dari luar
perangkat. Lihat `adapter.py`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from fleetview_common import ValidationError

__all__ = [
    "CAN_MODULE_ADDRESS_MAX",
    "CAN_MODULE_ADDRESS_MIN",
    "CAN_RECOMMENDED_MAX_MODULES",
    "CAN_WORDS_PER_MODULE",
    "UB_RANGES",
    "UW_RANGES",
    "CanArea",
    "DeviceArea",
    "UwRange",
    "can_module_range",
    "parse_ub_address",
    "ub_address",
    "uw_area_of",
]


class DeviceArea(StrEnum):
    """Manual §9.1.1 Device Component Description."""

    READ = "read"
    """Menampilkan status produk."""
    WRITE = "write"
    """Mengendalikan fungsi produk."""
    USER = "user"
    """Dipakai saat menggunakan fungsi produk dan *universal communication*.

    Catatan: istilah "universal communication" muncul tepat satu kali di seluruh
    151 halaman manual dan tidak pernah dijelaskan. Ini masih pertanyaan terbuka
    ke Autonics — bisa jadi inilah pintu masuk untuk membaca dari luar.
    """


@dataclass(frozen=True, slots=True)
class UwRange:
    """Rentang alamat word, inklusif di kedua ujung."""

    area: DeviceArea
    start: int
    end: int

    @property
    def word_count(self) -> int:
        return self.end - self.start + 1

    @property
    def bit_count(self) -> int:
        return self.word_count * BITS_PER_WORD

    def contains(self, address: int) -> bool:
        return self.start <= address <= self.end


BITS_PER_WORD: Final[int] = 16
"""Manual §9.1.2.3: satu word UW terdiri atas 16 bit UB, dinomori 0 sampai F."""

# Manual §9.1.2.2 Word Device Range
UW_RANGES: Final[tuple[UwRange, ...]] = (
    UwRange(DeviceArea.READ, 0, 80),  # 81 word
    UwRange(DeviceArea.WRITE, 81, 99),  # 19 word
    UwRange(DeviceArea.USER, 100, 128_999),  # 128.900 word
)

# Manual §9.1.2.1 Bit Device Range — diturunkan dari rentang word di atas.
# Diverifikasi terhadap angka yang tercetak: 81×16=1.296, 19×16=304,
# 128.900×16=2.062.400 — ketiganya cocok dengan manual.
UB_RANGES: Final[dict[DeviceArea, int]] = {r.area: r.bit_count for r in UW_RANGES}

_UB_PATTERN = re.compile(r"^UB(\d+)([0-9A-F])$", re.IGNORECASE)
"""Catatan soal padding nol:

Manual menampilkan alamat dengan padding nol yang **tidak konsisten** — READ AREA
ditulis `UW000000 to UW000080` (6 digit) sementara WRITE AREA ditulis
`UW0000081 to UW0000099` (7 digit), dan bit area ditulis `UB0000000 to UB000080F`.

Karena protokol transport-nya belum diketahui, bentuk string alamat masih menjadi
konvensi kita sendiri. Di sini dipakai bentuk **tanpa padding** (`UB1000`,
`UB80F`) karena tidak ambigu saat diurai. Kalau nanti ternyata protokol yang
sesungguhnya menuntut lebar tetap, formatternya diubah di sini — dan hanya di sini.
"""


def uw_area_of(address: int) -> DeviceArea:
    """Area mana yang memuat alamat word ini.

    Raises:
        ValidationError: alamat di luar seluruh rentang terdokumentasi.
    """
    for rng in UW_RANGES:
        if rng.contains(address):
            return rng.area
    raise ValidationError(
        f"alamat UW{address} di luar rentang terdokumentasi (UW0–UW128999)",
        code="lp_a104.address_out_of_range",
        details={"address": address},
    )


def ub_address(word: int, bit: int) -> str:
    """Susun alamat bit dari alamat word dan nomor bit.

    Manual §9.1.2.3: bit dipetakan ke satuan bit di dalam word, dengan nomor bit
    ditulis sebagai satu digit heksadesimal yang diimbuhkan ke alamat word.
    Contoh yang tercetak di manual: UW100 -> UB1000 (bit 0) sampai UB100F (bit F).

    Raises:
        ValidationError: nomor bit di luar 0–15, atau alamat word tidak valid.
    """
    if not 0 <= bit < BITS_PER_WORD:
        raise ValidationError(
            f"nomor bit {bit} di luar jangkauan 0–15",
            code="lp_a104.bit_out_of_range",
            details={"word": word, "bit": bit},
        )
    uw_area_of(word)  # memvalidasi alamat word
    return f"UB{word}{bit:X}"


def parse_ub_address(address: str) -> tuple[int, int]:
    """Uraikan alamat bit menjadi (alamat word, nomor bit).

    Kebalikan dari `ub_address`.

    Raises:
        ValidationError: bentuk alamat tidak sesuai.
    """
    match = _UB_PATTERN.match(address.strip())
    if match is None:
        raise ValidationError(
            f"alamat UB tidak valid: {address!r}. Bentuknya UB<word><bit heks>, mis. UB100F",
            code="lp_a104.bad_ub_address",
            details={"address": address},
        )
    word = int(match.group(1))
    bit = int(match.group(2), 16)
    uw_area_of(word)
    return word, bit


# --------------------------------------------------------------------------
# Peta memori CAN / DeviceNet — Manual §9.2
# --------------------------------------------------------------------------


class CanArea(StrEnum):
    """Tiga area yang dipetakan CAN. Manual §9.2.1."""

    INPUT = "input"
    OUTPUT_COMMAND = "output_command"
    OUTPUT_CURRENT = "output_current"


# Manual §9.2.1: masing-masing 512 byte = (63 slave + 1) x 8 byte per slave.
_CAN_AREA_BASE: Final[dict[CanArea, int]] = {
    CanArea.INPUT: 65_536,  # UW65536–UW65791
    CanArea.OUTPUT_COMMAND: 65_792,  # UW65792–UW66047
    CanArea.OUTPUT_CURRENT: 66_048,  # UW66048–UW66303
}

CAN_WORDS_PER_MODULE: Final[int] = 4
"""8 byte per slave = 4 word. Manual §9.2.1."""

CAN_MODULE_ADDRESS_MIN: Final[int] = 1
CAN_MODULE_ADDRESS_MAX: Final[int] = 63
"""Manual §1.8.4: "Max. number of connectable slave is 63. The address 0 is not
used and the 1 to 63 is used."

Catatan ketidaksesuaian di manual: §9.2.2 menuliskan "Maximum number of
connectable slaves is 64". Yang dipakai di sini adalah 63, karena angka itu
disebut eksplisit di §1.8.4 dan konsisten dengan rumus ukuran area yang tercetak
tiga kali: (63+1) x 8 byte = 512 byte. Yang "+1" adalah address 0 yang dicadangkan.
"""

CAN_RECOMMENDED_MAX_MODULES: Final[int] = 8
"""Manual §1.8.4 dan §9.2.2 sama-sama menganjurkan maksimal 8 unit demi
kestabilan sistem, meskipun secara teknis 63 dimungkinkan. Angka inilah yang
relevan saat merancang instalasi di kapal."""


def can_module_range(area: CanArea, module_address: int) -> UwRange:
    """Rentang word UW yang ditempati satu modul CAN.

    Rumus `base + 4 x module_address` diturunkan dari tabel di manual dan
    diverifikasi terhadap baris-baris yang tercetak:
      IN modul 0  -> UW65536–65539      IN modul 1 -> UW65540–65543
      OUT(command) modul 23 -> UW65884–65887
      OUT(command) modul 60 -> UW66032–66035

    Args:
        area: area CAN yang dimaksud.
        module_address: alamat modul, 1–63. Address 0 dicadangkan.

    Raises:
        ValidationError: alamat modul di luar jangkauan terdokumentasi.
    """
    if not CAN_MODULE_ADDRESS_MIN <= module_address <= CAN_MODULE_ADDRESS_MAX:
        raise ValidationError(
            f"alamat modul CAN {module_address} di luar jangkauan "
            f"{CAN_MODULE_ADDRESS_MIN}–{CAN_MODULE_ADDRESS_MAX} "
            f"(address 0 dicadangkan menurut manual §1.8.4)",
            code="lp_a104.can_module_out_of_range",
            details={"module_address": module_address},
        )
    base = _CAN_AREA_BASE[area]
    start = base + CAN_WORDS_PER_MODULE * module_address
    return UwRange(DeviceArea.USER, start, start + CAN_WORDS_PER_MODULE - 1)
