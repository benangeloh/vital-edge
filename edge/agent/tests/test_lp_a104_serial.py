"""Test parameter serial LP-A104.

Nilai-nilai ini disalin dari manual §5.6.1.1. Validasinya berguna sekarang meski
transport-nya belum ditentukan: begitu teknisi memberi tahu setelan yang
terpasang di kapal, salah ketik langsung ketahuan — bukan setelah seseorang
menghabiskan sehari mengejar bus yang diam.
"""

from __future__ import annotations

import pytest

from fleetview_common import ConfigError
from fleetview_edge.protocol.lp_a104 import FlowControl, Parity, SerialParams
from fleetview_edge.protocol.lp_a104.serial_params import (
    BAUD_RATES,
    CAN_BITRATE_BPS,
    GROUP_PACK_GAP_MAX,
)


class TestSerialParams:
    def test_default_mengikuti_setelan_atdesigner_di_manual(self) -> None:
        """Manual §8.1.3: 115200 / 8 bit / EVEN / 1 stop / tanpa flow control.

        Itu setelan kanal EDITOR, belum tentu kanal sensor — jadi ini titik awal
        yang masuk akal, bukan asumsi yang bisa dipercaya.
        """
        p = SerialParams()
        assert (p.baud_rate, p.data_bits, p.parity, p.stop_bits) == (
            115_200,
            8,
            Parity.EVEN,
            1,
        )
        assert p.flow_control is FlowControl.NONE

    @pytest.mark.parametrize("baud", sorted(BAUD_RATES))
    def test_semua_baud_rate_dari_manual_diterima(self, baud: int) -> None:
        assert SerialParams(baud_rate=baud).baud_rate == baud

    @pytest.mark.parametrize("baud", [110, 14400, 230400, 0, -1])
    def test_baud_rate_di_luar_manual_ditolak(self, baud: int) -> None:
        with pytest.raises(ConfigError, match="baud_rate"):
            SerialParams(baud_rate=baud)

    @pytest.mark.parametrize("bits", [5, 6, 9])
    def test_data_bit_tidak_didukung_ditolak(self, bits: int) -> None:
        """Manual hanya menyebut 7 dan 8."""
        with pytest.raises(ConfigError, match="data_bits"):
            SerialParams(data_bits=bits)

    @pytest.mark.parametrize("bits", [0, 3])
    def test_stop_bit_tidak_didukung_ditolak(self, bits: int) -> None:
        with pytest.raises(ConfigError, match="stop_bits"):
            SerialParams(stop_bits=bits)

    @pytest.mark.parametrize("timeout", [-1, 65_536])
    def test_timeout_di_luar_jangkauan_ditolak(self, timeout: int) -> None:
        """Manual: jangkauan setelan 0 sampai 65535."""
        with pytest.raises(ConfigError, match="timeout_ms"):
            SerialParams(timeout_ms=timeout)

    @pytest.mark.parametrize("retry", [-1, 65_536])
    def test_retry_di_luar_jangkauan_ditolak(self, retry: int) -> None:
        with pytest.raises(ConfigError, match="retry"):
            SerialParams(retry=retry)

    def test_beberapa_masalah_dilaporkan_sekaligus(self) -> None:
        """Operator yang memperbaiki config di kapal sebaiknya melihat semua
        yang salah sekali jalan, bukan satu per satu setiap kali restart."""
        with pytest.raises(ConfigError) as exc:
            SerialParams(baud_rate=110, data_bits=9, stop_bits=3)
        problems = exc.value.details["problems"]
        assert len(problems) == 3

    def test_setelan_valid_yang_tidak_default_diterima(self) -> None:
        p = SerialParams(
            baud_rate=9600,
            data_bits=8,
            parity=Parity.NONE,
            stop_bits=1,
            flow_control=FlowControl.XON_XOFF,
        )
        assert p.baud_rate == 9600


class TestKonstantaManual:
    def test_can_bitrate_tetap_500kbps(self) -> None:
        """Manual §5.6.1.1 (5): "Fixed 'Communication mode: 500kbps'"."""
        assert CAN_BITRATE_BPS == 500_000

    def test_group_pack_gap_maksimum_16(self) -> None:
        """Manual §5.6.1.2 (4): jangkauan setelan 1 sampai 16."""
        assert GROUP_PACK_GAP_MAX == 16
