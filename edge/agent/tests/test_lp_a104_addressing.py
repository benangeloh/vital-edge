"""Test model alamat LP-A104.

Test-test ini menjaga satu hal: bahwa angka-angka di `addressing.py` tetap cocok
dengan yang tercetak di manual. Setiap assertion di bawah bisa dicocokkan ke
halaman manual — kalau ada yang mengubah konstanta, test ini yang menangkapnya.
"""

from __future__ import annotations

import pytest

from fleetview_common import ValidationError
from fleetview_edge.protocol.lp_a104.addressing import (
    CAN_MODULE_ADDRESS_MAX,
    CAN_RECOMMENDED_MAX_MODULES,
    UW_RANGES,
    CanArea,
    DeviceArea,
    can_module_range,
    parse_ub_address,
    ub_address,
    uw_area_of,
)


class TestRentangDevice:
    """Manual §9.1.2 Device Range."""

    @pytest.mark.parametrize(
        ("address", "expected"),
        [
            (0, DeviceArea.READ),
            (80, DeviceArea.READ),
            (81, DeviceArea.WRITE),
            (99, DeviceArea.WRITE),
            (100, DeviceArea.USER),
            (128_999, DeviceArea.USER),
        ],
    )
    def test_batas_area_sesuai_manual(self, address: int, expected: DeviceArea) -> None:
        assert uw_area_of(address) is expected

    def test_alamat_di_luar_jangkauan_ditolak(self) -> None:
        with pytest.raises(ValidationError, match="di luar rentang"):
            uw_area_of(129_000)

    def test_jumlah_word_cocok_dengan_manual(self) -> None:
        """81 / 19 / 128.900 word — ketiganya tercetak di §9.1.2.2."""
        counts = {r.area: r.word_count for r in UW_RANGES}
        assert counts == {
            DeviceArea.READ: 81,
            DeviceArea.WRITE: 19,
            DeviceArea.USER: 128_900,
        }

    def test_jumlah_bit_cocok_dengan_manual(self) -> None:
        """1.296 / 304 / 2.062.400 bit — tercetak di §9.1.2.1.

        Ini pemeriksaan silang yang berguna: kalau rentang word salah, jumlah
        bit-nya tidak akan cocok dengan angka yang tercetak terpisah di manual.
        """
        counts = {r.area: r.bit_count for r in UW_RANGES}
        assert counts == {
            DeviceArea.READ: 1_296,
            DeviceArea.WRITE: 304,
            DeviceArea.USER: 2_062_400,
        }


class TestAlamatBit:
    """Manual §9.1.2.3 Word/Bit device address inclusion."""

    def test_contoh_yang_tercetak_di_manual(self) -> None:
        """Manual memakai UW100 sebagai contoh: UB1000 (bit 0) ... UB100F (bit F)."""
        assert ub_address(100, 0) == "UB1000"
        assert ub_address(100, 15) == "UB100F"
        assert ub_address(100, 3) == "UB1003"

    def test_bolak_balik(self) -> None:
        for word, bit in [(100, 0), (100, 15), (80, 7), (128_999, 15)]:
            assert parse_ub_address(ub_address(word, bit)) == (word, bit)

    def test_batas_area_read_cocok_dengan_manual(self) -> None:
        """Manual: READ AREA UB0000000 sampai UB000080F."""
        assert ub_address(0, 0) == "UB00"
        assert ub_address(80, 15) == "UB80F"

    @pytest.mark.parametrize("bit", [-1, 16, 99])
    def test_nomor_bit_di_luar_jangkauan_ditolak(self, bit: int) -> None:
        with pytest.raises(ValidationError, match="bit"):
            ub_address(100, bit)

    @pytest.mark.parametrize("bad", ["UW100", "UB100G", "100F", "", "UB"])
    def test_alamat_ub_salah_bentuk_ditolak(self, bad: str) -> None:
        with pytest.raises(ValidationError):
            parse_ub_address(bad)


class TestPetaMemoriCan:
    """Manual §9.2 CAN Memory Mapping.

    Rumusnya `base + 4 x module_address`. Baris-baris di bawah disalin langsung
    dari tabel yang tercetak, dan itulah yang membuat rumus ini bukan tebakan.
    """

    @pytest.mark.parametrize(
        ("area", "module", "start", "end"),
        [
            # Manual §9.2.2.1 Input Device Area
            (CanArea.INPUT, 1, 65_540, 65_543),
            (CanArea.INPUT, 2, 65_544, 65_547),
            (CanArea.INPUT, 3, 65_548, 65_551),
            # Manual §9.2.2.2 Output Device Command Area
            (CanArea.OUTPUT_COMMAND, 23, 65_884, 65_887),
            (CanArea.OUTPUT_COMMAND, 60, 66_032, 66_035),
        ],
    )
    def test_cocok_dengan_baris_tabel_yang_tercetak(
        self, area: CanArea, module: int, start: int, end: int
    ) -> None:
        rng = can_module_range(area, module)
        assert (rng.start, rng.end) == (start, end)

    def test_setiap_modul_menempati_empat_word(self) -> None:
        """Manual §9.2.1: 8 byte per slave = 4 word."""
        assert can_module_range(CanArea.INPUT, 5).word_count == 4

    def test_area_tidak_saling_tumpang_tindih(self) -> None:
        """Ketiga area masing-masing 512 byte = 256 word, bersambungan."""
        last_input = can_module_range(CanArea.INPUT, CAN_MODULE_ADDRESS_MAX)
        first_cmd = can_module_range(CanArea.OUTPUT_COMMAND, 1)
        assert last_input.end < first_cmd.start

    @pytest.mark.parametrize("module", [0, 64, -1, 100])
    def test_alamat_modul_tidak_valid_ditolak(self, module: int) -> None:
        """Address 0 dicadangkan, 64+ di luar jangkauan (manual §1.8.4: 1–63)."""
        with pytest.raises(ValidationError, match="modul CAN"):
            can_module_range(CanArea.INPUT, module)

    def test_batas_anjuran_lebih_ketat_dari_batas_teknis(self) -> None:
        """Manual menganjurkan maksimal 8 unit demi kestabilan, meski 63 mungkin."""
        assert CAN_RECOMMENDED_MAX_MODULES < CAN_MODULE_ADDRESS_MAX
