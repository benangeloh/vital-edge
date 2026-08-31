"""Parameter komunikasi LP-A Series — LANGSUNG DARI MANUAL.

Sumber: `docs/hardware/MTL-LPAU1-V3.3-EN_20260325_W.pdf`
        §5.6.1.1 The detailed settings for each interface

Nilai-nilai ini ada gunanya sekarang meski transport-nya belum ditentukan: begitu
teknisi memberi tahu setelan yang terpasang di kapal, kita bisa memvalidasinya
terhadap apa yang sebenarnya didukung perangkat — dan menangkap salah ketik
sebelum seseorang menghabiskan sehari mengejar bus yang diam.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from fleetview_common import ConfigError

__all__ = [
    "BAUD_RATES",
    "CAN_BITRATE_BPS",
    "DATA_BITS",
    "ETHERNET_PORT_MAX",
    "ETHERNET_PORT_MIN",
    "GROUP_PACK_GAP_MAX",
    "GROUP_PACK_GAP_MIN",
    "RETRY_MAX",
    "STOP_BITS",
    "TIMEOUT_MAX",
    "FlowControl",
    "Parity",
    "SerialParams",
]

# Manual §5.6.1.1 (1) RS-422/485 dan (2) RS-232C
BAUD_RATES: Final[frozenset[int]] = frozenset(
    {300, 600, 900, 1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200}
)
DATA_BITS: Final[frozenset[int]] = frozenset({7, 8})
STOP_BITS: Final[frozenset[int]] = frozenset({1, 2})

TIMEOUT_MAX: Final[int] = 65_535
RETRY_MAX: Final[int] = 65_535

# Manual §5.6.1.1 (3) Ethernet
ETHERNET_PORT_MIN: Final[int] = 0
ETHERNET_PORT_MAX: Final[int] = 65_535

GROUP_PACK_GAP_MIN: Final[int] = 1
GROUP_PACK_GAP_MAX: Final[int] = 16
"""Manual §5.6.1.2 (4): mengelompokkan device yang berdekatan agar jumlah
transaksi komunikasi berkurang. Manual memperingatkan: bila PLC tidak mendukung
seluruh jangkauan alamat, nilai besar bisa memicu error komunikasi karena
menyentuh device yang tidak ada — kalau begitu setel ke 1."""

# Manual §5.6.1.1 (5) DEVICE-NET (CAN): "Fixed 'Communication mode: 500kbps'".
CAN_BITRATE_BPS: Final[int] = 500_000


class Parity(StrEnum):
    NONE = "none"
    ODD = "odd"
    EVEN = "even"


class FlowControl(StrEnum):
    NONE = "none"
    XON_XOFF = "xon_xoff"
    DSR_DTR = "dsr_dtr"


@dataclass(frozen=True, slots=True)
class SerialParams:
    """Setelan port serial, divalidasi terhadap yang didukung perangkat.

    Nilai default mengikuti setelan sambungan atDesigner yang tercetak di manual
    §8.1.3 (115200 / 8 / EVEN / 1 / tanpa flow control). Itu setelan untuk kanal
    EDITOR, bukan tentu untuk kanal sensor — jadi ini titik awal yang masuk akal,
    bukan asumsi yang bisa dipercaya. Setelan sebenarnya harus dikonfirmasi ke
    lapangan.
    """

    baud_rate: int = 115_200
    data_bits: int = 8
    parity: Parity = Parity.EVEN
    stop_bits: int = 1
    flow_control: FlowControl = FlowControl.NONE
    timeout_ms: int = 1_000
    retry: int = 3

    def __post_init__(self) -> None:
        problems: list[str] = []
        if self.baud_rate not in BAUD_RATES:
            problems.append(
                f"baud_rate {self.baud_rate} tidak didukung; pilih dari {sorted(BAUD_RATES)}"
            )
        if self.data_bits not in DATA_BITS:
            problems.append(f"data_bits {self.data_bits} tidak didukung; pilih 7 atau 8")
        if self.stop_bits not in STOP_BITS:
            problems.append(f"stop_bits {self.stop_bits} tidak didukung; pilih 1 atau 2")
        if not 0 <= self.timeout_ms <= TIMEOUT_MAX:
            problems.append(f"timeout_ms {self.timeout_ms} di luar 0–{TIMEOUT_MAX}")
        if not 0 <= self.retry <= RETRY_MAX:
            problems.append(f"retry {self.retry} di luar 0–{RETRY_MAX}")
        if problems:
            raise ConfigError(
                "parameter serial tidak sesuai yang didukung LP-A104: " + "; ".join(problems),
                code="lp_a104.serial_params_invalid",
                details={"problems": problems},
            )
