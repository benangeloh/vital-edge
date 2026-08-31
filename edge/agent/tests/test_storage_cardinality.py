"""Test penjagaan kardinalitas.

Ledakan kardinalitas membunuh InfluxDB secara perlahan — penulisan melambat,
memori naik, lalu proses di-OOM-kill. Di kapal itu berarti kehilangan data tanpa
ada yang menyadarinya sampai berminggu-minggu kemudian.
"""

from __future__ import annotations

import pytest

from fleetview_common import ValidationError
from fleetview_edge.storage import FORBIDDEN_TAG_KEYS, estimate_series, verify_tag_keys
from fleetview_edge.storage.cardinality import MAX_EXTRA_TAGS


class TestKunciTerlarang:
    @pytest.mark.parametrize(
        "key", ["timestamp", "seq", "sequence_number", "batch_id", "value", "session_id"]
    )
    def test_kunci_unik_per_pembacaan_ditolak(self, key: str) -> None:
        """Masing-masing akan membuat satu seri baru setiap detik, selamanya."""
        with pytest.raises(ValidationError, match="dilarang"):
            verify_tag_keys({key: "x"})

    def test_pemeriksaan_tidak_peka_huruf_besar_kecil(self) -> None:
        with pytest.raises(ValidationError):
            verify_tag_keys({"Timestamp": "x"})

    def test_tag_yang_sah_diterima(self) -> None:
        verify_tag_keys({"engine": "port", "point": "exhaust", "tank": "main"})

    def test_pesan_error_menyebutkan_kunci_yang_salah(self) -> None:
        with pytest.raises(ValidationError) as exc:
            verify_tag_keys({"batch_id": "x", "engine": "port"})
        assert exc.value.details["forbidden"] == ["batch_id"]

    def test_konteks_disertakan_agar_mudah_dilacak(self) -> None:
        with pytest.raises(ValidationError, match="sensor me_port_rpm"):
            verify_tag_keys({"seq": "1"}, context="sensor me_port_rpm")


class TestBatasJumlahTag:
    def test_tepat_di_batas_diterima(self) -> None:
        verify_tag_keys({f"t{i}": "v" for i in range(MAX_EXTRA_TAGS)})

    def test_melebihi_batas_ditolak(self) -> None:
        """Sensor yang butuh sebanyak ini hampir pasti menyelundupkan data
        pengukuran ke dalam tag."""
        with pytest.raises(ValidationError, match="melebihi batas"):
            verify_tag_keys({f"t{i}": "v" for i in range(MAX_EXTRA_TAGS + 1)})


class TestAnggaranSeri:
    def test_armada_saat_ini_jauh_di_bawah_batas_aman(self) -> None:
        """70 kapal x 80 sensor x quality x source.

        Batas praktis InfluxDB OSS ada di kisaran jutaan seri; angka kita harus
        tetap jauh di bawahnya bahkan setelah armada bertambah.
        """
        assert estimate_series(ships=70, sensors_per_ship=80) == 89_600

    def test_target_pertumbuhan_masih_aman(self) -> None:
        """Ruang tumbuh desain: 200 kapal."""
        assert estimate_series(ships=200, sensors_per_ship=100) == 320_000

    def test_satu_tag_berkardinalitas_tinggi_merusak_semuanya(self) -> None:
        """Ini alasan FORBIDDEN_TAG_KEYS ada.

        Satu tag unik-per-pembacaan menaikkan jumlah seri dari puluhan ribu
        menjadi puluhan miliar — dan tidak ada yang menyadarinya sampai InfluxDB
        mulai kehabisan memori.
        """
        aman = estimate_series(ships=70, sensors_per_ship=80)
        # Anggap satu tag berisi sequence: nilai unik per pembacaan, 1 Hz, 30 hari.
        pembacaan_per_sensor = 86_400 * 30
        assert aman * pembacaan_per_sensor > 200_000_000_000

    def test_semua_kunci_terlarang_huruf_kecil(self) -> None:
        """Pemeriksaan menormalkan input ke huruf kecil, jadi daftarnya harus
        huruf kecil semua — kalau tidak, ada entri yang tidak pernah cocok."""
        assert all(k == k.lower() for k in FORBIDDEN_TAG_KEYS)
