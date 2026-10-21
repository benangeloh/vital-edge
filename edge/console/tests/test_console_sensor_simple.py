"""Test formulir sensor sederhana dan pencarian sensor di Console.

Teknisi cukup memilih jenis, menamai, dan mengisi alamat. Formulir lengkap tetap
ada, tetapi tersembunyi di "Pengaturan lanjutan".

Context palsu bawaan tidak punya preset maupun pencarian; test ini menambahkannya
ke instance, sehingga conftest.py tidak perlu berubah.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any, ClassVar

import pytest
from fastapi.testclient import TestClient

from fleetview_common import ValidationError
from fleetview_console import create_console_app

PRESETS = [
    {
        "id": "rpm",
        "label": "RPM mesin",
        "name_hint": "me_rpm",
        "verified": False,
        "note": "Skala 1:1 masih asumsi; cocokkan ke layar HMI saat mesin berjalan.",
    },
    {
        "id": "lo_press",
        "label": "Tekanan oli pelumas (L.O)",
        "name_hint": "lo_press",
        "verified": True,
        "note": None,
    },
]

TEMUAN = [
    {"channel": "UW50", "value": 1510, "changing": True, "likely_sensor": True},
    {"channel": "UW20", "value": 2, "changing": False, "likely_sensor": False},
]


def _client(context: Any) -> TestClient:
    app = create_console_app(
        context=context,
        ship_name="KM Sinar Jaya",
        ship_id="11111111-1111-1111-1111-111111111111",
        agent_version="0.1.0",
        environment="production",
    )
    return TestClient(app)


@pytest.fixture
def ctx(make_context: Callable[..., Any]) -> Any:
    context = make_context(adapter="lp_a104")
    context.added = []
    context.sensor_presets = lambda: PRESETS

    def add_sensor_simple(**kw: Any) -> str:
        context.added.append(kw)
        return kw["sensor_id"]

    async def discover_channels() -> list[dict[str, Any]]:
        return TEMUAN

    context.add_sensor_simple = add_sensor_simple
    context.discover_channels = discover_channels
    return context


class TestFormulirSederhana:
    def test_hanya_tiga_isian_terlihat_dan_lainnya_di_pengaturan_lanjutan(self, ctx: Any) -> None:
        html = _client(ctx).get("/sensors").text

        sederhana = html.split('action="/sensors/tambah"', 1)[1].split("</form>", 1)[0]
        assert 'name="jenis"' in sederhana
        assert 'name="sensor_id"' in sederhana
        assert 'name="alamat"' in sederhana
        for rinci in ("scale", "offset", "min_value", "max_value", "metric", "field_name"):
            assert f'name="{rinci}"' not in sederhana
        assert "Pengaturan lanjutan" in html

    def test_formulir_lengkap_tetap_ada_di_dalam_details(self, ctx: Any) -> None:
        html = _client(ctx).get("/sensors").text

        lanjutan = html.split("<details", 1)[1].split("</details>", 1)[0]
        assert 'action="/sensors/simpan"' in lanjutan
        assert 'name="scale"' in lanjutan

    def test_jenis_sensor_ditampilkan_sebagai_pilihan(self, ctx: Any) -> None:
        html = _client(ctx).get("/sensors").text

        assert '<option value="rpm">RPM mesin</option>' in html
        assert "Tekanan oli pelumas (L.O)" in html

    def test_skala_yang_belum_dicocokkan_diberi_catatan(self, ctx: Any) -> None:
        html = _client(ctx).get("/sensors").text

        assert "Cocokkan ke layar HMI" in html
        assert "RPM mesin" in html

    def test_lp_a104_meminta_alamat_sumber_di_panel(self, ctx: Any) -> None:
        html = _client(ctx).get("/sensors").text

        assert "Alamat sumber di panel" in html
        assert "dipilih otomatis" in html

    def test_adapter_lain_meminta_alamat_biasa(self, make_context: Callable[..., Any]) -> None:
        context = make_context(adapter="simulator")
        context.sensor_presets = lambda: PRESETS
        html = _client(context).get("/sensors").text

        assert "Alamat sumber di panel" not in html
        assert "UW0–UW128999" not in html

    def test_tanpa_preset_kembali_ke_formulir_lengkap(
        self, make_context: Callable[..., Any]
    ) -> None:
        html = _client(make_context()).get("/sensors").text

        assert 'action="/sensors/tambah"' not in html
        assert 'action="/sensors/simpan"' in html
        assert "Tambah sensor" in html

    def test_mengubah_sensor_langsung_menampilkan_formulir_lengkap(self, ctx: Any) -> None:
        html = _client(ctx).get("/sensors?edit=me_port_rpm").text

        assert "Ubah sensor me_port_rpm" in html
        assert 'action="/sensors/tambah"' not in html

    def test_peringatan_lp_a104_tetap_tampil_dan_akurat(self, ctx: Any) -> None:
        html = _client(ctx).get("/sensors").text

        assert "belum terkonfirmasi ke vendor" in html
        assert "pengujian lapangan" in html


class TestJudulHalaman:
    def test_judul_tab_tidak_tercemar_markup_formulir(self, ctx: Any) -> None:
        """Daftar dan formulir sempat berada di dalam block title, sehingga judul
        tab browser berisi `<section class="card">…`."""
        html = _client(ctx).get("/sensors").text

        judul = re.search(r"<title>(.*?)</title>", html, re.S)
        assert judul is not None
        assert "<" not in judul.group(1)
        assert judul.group(1).strip().startswith("Sensor")

    def test_daftar_sensor_hanya_muncul_sekali(self, ctx: Any) -> None:
        html = _client(ctx).get("/sensors").text

        assert html.count("Daftar sensor terpasang") == 1
        assert html.count('action="/sensors/hapus"') == 1


class TestTambah:
    def test_meneruskan_isian_ke_context(self, ctx: Any) -> None:
        r = _client(ctx).post(
            "/sensors/tambah",
            data={"jenis": "rpm", "sensor_id": " me_rpm ", "alamat": "D160", "channel": ""},
            follow_redirects=False,
        )

        assert r.status_code == 303
        assert r.headers["location"] == "/sensors?disimpan=1"
        assert ctx.added == [
            {"jenis": "rpm", "sensor_id": "me_rpm", "alamat": "D160", "channel": ""}
        ]

    def test_channel_hasil_pencarian_ikut_diteruskan(self, ctx: Any) -> None:
        _client(ctx).post(
            "/sensors/tambah",
            data={"jenis": "rpm", "sensor_id": "a", "channel": "UW50"},
            follow_redirects=False,
        )

        assert ctx.added[0]["channel"] == "UW50"
        assert ctx.added[0]["alamat"] == ""

    def test_penolakan_ditampilkan_sebagai_pesan(self, ctx: Any) -> None:
        def tolak(**_kw: Any) -> str:
            raise ValidationError("nama sensor 'a' sudah dipakai", code="sensor.nama_dipakai")

        ctx.add_sensor_simple = tolak

        r = _client(ctx).post(
            "/sensors/tambah",
            data={"jenis": "rpm", "sensor_id": "a", "alamat": "D160"},
            follow_redirects=False,
        )

        assert r.status_code == 303
        assert "sudah%20dipakai" in r.headers["location"]

    def test_context_tanpa_dukungan_ditolak_dengan_pesan(
        self, make_context: Callable[..., Any]
    ) -> None:
        r = _client(make_context()).post(
            "/sensors/tambah",
            data={"jenis": "rpm", "sensor_id": "a", "alamat": "D160"},
            follow_redirects=False,
        )

        assert r.status_code == 303
        assert "error=" in r.headers["location"]


class TestCari:
    def test_tombol_cari_tampil_bila_context_mendukung(self, ctx: Any) -> None:
        html = _client(ctx).get("/sensors").text

        assert 'action="/sensors/cari"' in html

    def test_tombol_cari_tersembunyi_bila_tidak_didukung(
        self, make_context: Callable[..., Any]
    ) -> None:
        assert 'action="/sensors/cari"' not in _client(make_context()).get("/sensors").text

    def test_hasil_ditampilkan_dengan_nilai_dan_status(self, ctx: Any) -> None:
        html = _client(ctx).post("/sensors/cari").text

        assert "UW50" in html
        assert "1510" in html
        assert "berubah" in html.lower()

    def test_tiap_hasil_bisa_dipakai_dengan_channel_tersembunyi(self, ctx: Any) -> None:
        html = _client(ctx).post("/sensors/cari").text

        assert 'name="channel" value="UW50"' in html
        assert 'action="/sensors/tambah"' in html

    def test_register_kemungkinan_bawaan_panel_dibedakan(self, ctx: Any) -> None:
        html = _client(ctx).post("/sensors/cari").text

        assert "kemungkinan bawaan panel" in html
        assert "kemungkinan sensor" in html

    def test_keterbatasan_dinyatakan_terus_terang(self, ctx: Any) -> None:
        html = _client(ctx).post("/sensors/cari").text

        assert "bernilai 0" in html
        assert "ladder" in html

    def test_tidak_ada_temuan_dijelaskan(self, ctx: Any) -> None:
        async def kosong() -> list[dict[str, Any]]:
            return []

        ctx.discover_channels = kosong

        html = _client(ctx).post("/sensors/cari").text

        assert "Tidak ada channel baru" in html

    def test_kegagalan_baca_menjadi_pesan_bukan_halaman_rusak(self, ctx: Any) -> None:
        async def gagal() -> list[dict[str, Any]]:
            raise RuntimeError("panel tidak membalas")

        ctx.discover_channels = gagal

        r = _client(ctx).post("/sensors/cari")

        assert r.status_code == 200
        assert "panel tidak membalas" in r.text
        assert "Daftar sensor terpasang" in r.text


def _formulir_tambah(html: str) -> str:
    return html.split('action="/sensors/tambah"', 1)[1].split("</form>", 1)[0]


class TestSatuFormulir:
    """Mencari dan menambah adalah satu alur, bukan dua kartu yang membingungkan."""

    def test_hanya_satu_formulir_tambah_tanpa_kartu_cari_terpisah(self, ctx: Any) -> None:
        html = _client(ctx).get("/sensors").text

        assert html.count('action="/sensors/tambah"') == 1
        assert "Cari sensor otomatis" not in html

    def test_tombol_cari_ada_di_dalam_formulir_yang_sama(self, ctx: Any) -> None:
        form = _formulir_tambah(_client(ctx).get("/sensors").text)

        assert 'formaction="/sensors/cari"' in form

    def test_tombol_cari_tidak_memicu_validasi_isian_lain(self, ctx: Any) -> None:
        """Mencari harus bisa ditekan sebelum nama dan jenis diisi."""
        form = _formulir_tambah(_client(ctx).get("/sensors").text)

        tombol = re.search(r"<button[^>]*formaction=\"/sensors/cari\"[^>]*>", form)
        assert tombol is not None
        assert "formnovalidate" in tombol.group(0)

    def test_dua_langkah_bernomor(self, ctx: Any) -> None:
        form = _formulir_tambah(_client(ctx).get("/sensors").text)

        assert "Alamat sensor" in form
        assert "Jenis dan nama" in form

    def test_alamat_ketik_tidak_wajib_karena_bisa_dipilih_dari_pencarian(self, ctx: Any) -> None:
        form = _formulir_tambah(_client(ctx).get("/sensors").text)

        isian = re.search(r"<input[^>]*name=\"alamat\"[^>]*>", form)
        assert isian is not None
        assert "required" not in isian.group(0)

    def test_hasil_pencarian_menjadi_pilihan_di_formulir_yang_sama(self, ctx: Any) -> None:
        html = _client(ctx).post("/sensors/cari").text

        assert html.count('action="/sensors/tambah"') == 1
        form = _formulir_tambah(html)
        assert 'type="radio" name="channel" value="UW50"' in form
        assert 'type="radio" name="channel" value="UW20"' in form

    def test_isian_yang_sudah_diketik_dipertahankan_setelah_mencari(self, ctx: Any) -> None:
        html = (
            _client(ctx)
            .post(
                "/sensors/cari",
                data={"jenis": "rpm", "sensor_id": "me_rpm", "alamat": "D160"},
            )
            .text
        )

        form = _formulir_tambah(html)
        assert 'value="me_rpm"' in form
        assert 'value="D160"' in form
        assert '<option value="rpm" selected>' in form


class TestTanpaTandaPisahPanjang:
    """Tanda pisah panjang di antarmuka terbaca seperti teks otomatis dan menyulitkan
    dibaca cepat. Penanda kosong memakai tanda hubung, kalimat memakai titik."""

    @pytest.mark.parametrize("path", ["/", "/sensors", "/sync", "/network", "/logs", "/config"])
    def test_halaman_tidak_memuatnya(self, ctx: Any, path: str) -> None:
        assert "—" not in _client(ctx).get(path).text

    def test_hasil_pencarian_tidak_memuatnya(self, ctx: Any) -> None:
        assert "—" not in _client(ctx).post("/sensors/cari").text

    def test_halaman_sensor_dengan_formulir_lengkap_tidak_memuatnya(
        self, make_context: Callable[..., Any]
    ) -> None:
        assert "—" not in _client(make_context()).get("/sensors?edit=me_port_rpm").text


class TestTabelTerdaftar:
    def _dengan(self, ctx: Any, entry: dict[str, Any]) -> str:
        ctx._registry = [entry]
        return _client(ctx).get("/sensors").text

    def test_sensor_tanpa_batas_dan_tanpa_skala_tidak_merusak_halaman(self, ctx: Any) -> None:
        html = self._dengan(ctx, {"sensor_id": "x", "channel": "UW50", "metric": "rpm"})

        assert "Tanpa batas" in html

    def test_angka_bulat_tampil_tanpa_nol_desimal(self, ctx: Any) -> None:
        html = self._dengan(
            ctx,
            {
                "sensor_id": "x",
                "channel": "UW50",
                "metric": "rpm",
                "scale": 1.0,
                "validation": {"min_value": 0.0, "max_value": 2000.0},
            },
        )

        assert "0 s/d 2000" in html
        assert "2000.0" not in html

    def test_batas_satu_sisi_ditulis_jelas(self, ctx: Any) -> None:
        html = self._dengan(
            ctx,
            {
                "sensor_id": "x",
                "channel": "UW50",
                "metric": "rpm",
                "validation": {"max_value": 90.5},
            },
        )

        assert "maks 90.5" in html

    def test_kolom_sumber_panel_hanya_untuk_lp_a104(self, make_context: Callable[..., Any]) -> None:
        sim = make_context(adapter="simulator")
        lp = make_context(adapter="lp_a104")

        assert "Sumber panel" not in _client(sim).get("/sensors").text
        assert "Sumber panel" in _client(lp).get("/sensors").text


class TestLayarSempit:
    def test_wadah_tabel_mengurung_teks_sr_only(self) -> None:
        """Teks sr-only berposisi absolut. Di dalam tabel yang bergulir, tanpa wadah
        berposisi ia mendarat di luar layar dan melebarkan seluruh halaman."""
        from pathlib import Path

        import fleetview_console

        css = (Path(fleetview_console.__file__).parent / "static" / "console.css").read_text()

        assert re.search(r"\.tablewrap[^{]*\{[^}]*position:\s*relative", css)
        assert re.search(r"\.card--flush[^{]*\{[^}]*position:\s*relative", css)


class TestCariTanpaPindahHalaman:
    """Tombol cari memperbarui hanya area hasil lewat fetch, dengan indikator
    memuat. Tanpa JavaScript, tombol tetap mengirim formulir seperti biasa."""

    HEADER: ClassVar[dict[str, str]] = {"X-Requested-With": "console"}

    def test_permintaan_fetch_mendapat_fragmen_bukan_halaman_penuh(self, ctx: Any) -> None:
        r = _client(ctx).post("/sensors/cari", headers=self.HEADER)

        assert r.status_code == 200
        assert "<html" not in r.text
        assert 'type="radio" name="channel" value="UW50"' in r.text

    def test_permintaan_biasa_tetap_halaman_penuh(self, ctx: Any) -> None:
        r = _client(ctx).post("/sensors/cari")

        assert "<html" in r.text
        assert "Daftar sensor terpasang" in r.text

    def test_fragmen_menampilkan_kegagalan_sebagai_pesan(self, ctx: Any) -> None:
        async def gagal() -> list[dict[str, Any]]:
            raise RuntimeError("panel tidak membalas")

        ctx.discover_channels = gagal

        r = _client(ctx).post("/sensors/cari", headers=self.HEADER)

        assert r.status_code == 200
        assert "panel tidak membalas" in r.text
        assert "<html" not in r.text

    def test_fragmen_tanpa_temuan_dijelaskan(self, ctx: Any) -> None:
        async def kosong() -> list[dict[str, Any]]:
            return []

        ctx.discover_channels = kosong

        assert (
            "Tidak ada channel baru" in _client(ctx).post("/sensors/cari", headers=self.HEADER).text
        )

    def test_area_hasil_dan_tombol_siap_dipakai_skrip(self, ctx: Any) -> None:
        form = _formulir_tambah(_client(ctx).get("/sensors").text)

        assert 'id="hasil-cari"' in form
        assert 'aria-live="polite"' in form
        tombol = re.search(r"<button[^>]*formaction=\"/sensors/cari\"[^>]*>", form)
        assert tombol is not None
        assert "data-cari" in tombol.group(0)

    def test_hasil_pencarian_penuh_dan_fragmen_memakai_markup_yang_sama(self, ctx: Any) -> None:
        client = _client(ctx)
        penuh = _formulir_tambah(client.post("/sensors/cari").text)
        fragmen = client.post("/sensors/cari", headers=self.HEADER).text

        assert fragmen.strip() in penuh

    def test_skrip_menangani_pencarian_dan_tetap_kecil(self, ctx: Any) -> None:
        js = _client(ctx).get("/static/console.js").text

        assert "data-cari" in js
        assert "X-Requested-With" in js
        assert "Mencari" in js
        assert len(js.encode()) < 4_000


class TestCacheAset:
    """Browser teknisi tidak boleh memakai CSS/JS lama setelah agent di-upgrade."""

    @pytest.mark.parametrize("path", ["/static/console.js", "/static/console.css"])
    def test_aset_wajib_divalidasi_ulang(self, ctx: Any, path: str) -> None:
        r = _client(ctx).get(path)

        assert r.status_code == 200
        assert r.headers["cache-control"] == "no-cache"
        assert r.headers.get("etag")

    def test_validasi_etag_menghasilkan_304_tanpa_isi(self, ctx: Any) -> None:
        client = _client(ctx)
        etag = client.get("/static/console.js").headers["etag"]

        r = client.get("/static/console.js", headers={"If-None-Match": etag})

        assert r.status_code == 304
        assert r.content == b""

    def test_halaman_html_tidak_ikut_diubah(self, ctx: Any) -> None:
        assert "cache-control" not in _client(ctx).get("/sensors").headers


class TestVersiAset:
    """Alamat CSS dan JS membawa versi isinya. Browser yang sudah menyimpan salinan
    lama sebelum header no-cache ada tidak akan memvalidasi ulang alamat yang sama,
    tetapi pasti mengambil alamat baru."""

    def test_halaman_merujuk_aset_dengan_versi(self, ctx: Any) -> None:
        html = _client(ctx).get("/sensors").text

        assert re.search(r'src="/static/console\.js\?v=[0-9a-f]{8,}"', html)
        assert re.search(r'href="/static/console\.css\?v=[0-9a-f]{8,}"', html)

    def test_versi_stabil_antar_permintaan(self, ctx: Any) -> None:
        client = _client(ctx)

        def versi(html: str) -> str:
            m = re.search(r"console\.js\?v=([0-9a-f]+)", html)
            assert m is not None
            return m.group(1)

        assert versi(client.get("/sensors").text) == versi(client.get("/").text)

    def test_alamat_berversi_tetap_dilayani_tanpa_cache(self, ctx: Any) -> None:
        r = _client(ctx).get("/static/console.js?v=abcdef12")

        assert r.status_code == 200
        assert r.headers["cache-control"] == "no-cache"

    def test_versi_berubah_bila_isi_aset_berubah(self, tmp_path: Any) -> None:
        from fleetview_console.app import asset_version

        (tmp_path / "console.css").write_text("a{}")
        (tmp_path / "console.js").write_text("var x=1;")
        awal = asset_version(tmp_path)

        assert awal == asset_version(tmp_path)

        (tmp_path / "console.js").write_text("var x=2;")
        assert asset_version(tmp_path) != awal

    def test_versi_berubah_bila_css_berubah(self, tmp_path: Any) -> None:
        from fleetview_console.app import asset_version

        (tmp_path / "console.css").write_text("a{}")
        (tmp_path / "console.js").write_text("var x=1;")
        awal = asset_version(tmp_path)

        (tmp_path / "console.css").write_text("b{}")

        assert asset_version(tmp_path) != awal
