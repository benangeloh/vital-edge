"""Test Edge Console.

Dua jaminan yang paling menentukan diuji di sini, dan keduanya tentang keadaan
buruk — karena itulah saat Console dibuka:

1. **Halaman tetap tampil ketika komponennya rusak.** Collector mati harus
   menghasilkan satu kartu berisi pesan error, bukan halaman yang gagal dimuat.
2. **Rahasia tidak pernah sampai ke layar.**
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from fleetview_console import create_console_app

PAGES = ["/", "/sensors", "/sync", "/export", "/network", "/logs", "/config"]


def make_client(context: Any) -> TestClient:
    app = create_console_app(
        context=context,
        ship_name="KM Sinar Jaya",
        ship_id="11111111-1111-1111-1111-111111111111",
        agent_version="0.1.0",
        environment="production",
    )
    return TestClient(app)


@pytest.fixture
def client(make_context: Callable[..., Any]) -> TestClient:
    return make_client(make_context())


class TestSemuaHalaman:
    @pytest.mark.parametrize("path", PAGES)
    def test_halaman_ter_render(self, client: TestClient, path: str) -> None:
        response = client.get(path)
        assert response.status_code == 200
        assert "KM Sinar Jaya" in response.text

    @pytest.mark.parametrize("path", PAGES)
    def test_setiap_halaman_punya_navigasi_lengkap(self, client: TestClient, path: str) -> None:
        text = client.get(path).text
        for href in PAGES:
            assert f'href="{href}"' in text

    def test_health_tanpa_dependency(self, client: TestClient) -> None:
        """Probe ini harus tetap menjawab justru saat bagian lain bermasalah —
        itulah saat ia dibaca."""
        body = client.get("/api/health").json()
        assert body["ok"] is True
        assert body["data"]["status"] == "alive"

    def test_openapi_dimatikan(self, client: TestClient) -> None:
        assert client.get("/docs").status_code == 404


class TestTetapTampilSaatRusak:
    """Satu komponen rusak tidak boleh menjatuhkan halaman."""

    @pytest.mark.parametrize(
        ("path", "broken"),
        [
            ("/", "system"),
            ("/", "sync"),
            ("/sensors", "sensors"),
            ("/sync", "sync"),
            ("/export", "export_targets"),
            ("/network", "network"),
            ("/logs", "logs"),
            ("/config", "config"),
        ],
    )
    def test_halaman_tetap_200_dan_menjelaskan(
        self, path: str, broken: str, make_context: Callable[..., Any]
    ) -> None:
        client = make_client(make_context(fail={broken}))
        response = client.get(path)
        assert response.status_code == 200
        assert "tidak tersedia" in response.text

    def test_ikhtisar_tetap_menampilkan_sync_saat_sistem_rusak(
        self, make_context: Callable[..., Any]
    ) -> None:
        """Bagian yang masih bisa dibaca harus tetap terbaca — itu inti
        pengambilan data per bagian."""
        client = make_client(make_context(fail={"system"}))
        text = client.get("/").text
        assert "Tidak bisa membaca status sistem" in text
        assert "18.400" in text  # data sync tetap tampil


class TestKeamanan:
    def test_rahasia_tidak_pernah_tampil(self, client: TestClient) -> None:
        text = client.get("/config").text
        assert "••••••••" in text
        for leaked in ("influx-token-rahasia", "device-secret", "password"):
            assert leaked not in text

    def test_nilai_belum_diisi_dibedakan_dari_tersembunyi(self, client: TestClient) -> None:
        """Membedakan "rahasia disembunyikan" dari "rahasia belum diisi" adalah
        informasi troubleshooting yang penting, dan tidak membocorkan apa pun."""
        text = client.get("/config").text
        assert "belum diisi" in text

    def test_config_hanya_baca(self, client: TestClient) -> None:
        assert client.post("/config").status_code in (404, 405)
        assert "/etc/fleetview/edge.yaml" in client.get("/config").text


class TestBekerjaTanpaJavascript:
    def test_aksi_memakai_form_post_bukan_fetch(self, client: TestClient) -> None:
        """Tombol harus tetap bekerja saat JavaScript mati — Console dibuka
        justru ketika banyak hal tidak bekerja."""
        assert '<form method="post" action="/sync/trigger">' in client.get("/sync").text
        assert 'action="/export/start"' in client.get("/export").text

    def test_penyaring_log_berupa_tautan(self, client: TestClient) -> None:
        text = client.get("/logs").text
        assert 'href="/logs?level=error"' in text

    def test_trigger_sync_memanggil_agent_lalu_mengalihkan(
        self, make_context: Callable[..., Any]
    ) -> None:
        context = make_context()
        client = make_client(context)
        response = client.post("/sync/trigger", follow_redirects=False)
        assert response.status_code == 303
        assert context.sync_triggered == 1

    def test_trigger_gagal_tetap_mengalihkan_dengan_pesan(
        self, make_context: Callable[..., Any]
    ) -> None:
        client = make_client(make_context(fail={"trigger_sync"}))
        response = client.post("/sync/trigger", follow_redirects=False)
        assert response.status_code == 303
        assert "error=1" in response.headers["location"]

    def test_ekspor_memanggil_agent(self, make_context: Callable[..., Any]) -> None:
        context = make_context()
        client = make_client(context)
        response = client.post(
            "/export/start", data={"target": "/media/usb0"}, follow_redirects=False
        )
        assert response.status_code == 303
        assert context.exports == ["/media/usb0"]


class TestKontenOperasional:
    def test_ikhtisar_menampilkan_semua_metrik_yang_disyaratkan(self, client: TestClient) -> None:
        text = client.get("/").text
        for expected in ("Collector", "InfluxDB", "Penyimpanan", "CPU", "Memori", "Suhu"):
            assert expected in text

    def test_sensor_menampilkan_nilai_kualitas_dan_umur(self, client: TestClient) -> None:
        text = client.get("/sensors").text
        assert "me_port_rpm" in text
        assert "742.5" in text
        assert "mencurigakan" in text  # kualitas suspect terlihat

    def test_sensor_terputus_ditandai(self, client: TestClient) -> None:
        assert "putus" in client.get("/sensors").text

    def test_sensor_multi_field_menampilkan_semua_nilai(self, client: TestClient) -> None:
        """GPS bernilai lat/lon/sog/cog. Menampilkan strip karena tidak ada
        "satu nilai" akan menyembunyikan tepat informasi yang dicari teknisi."""
        text = client.get("/sensors").text
        assert "-7.2575" in text
        assert "112.7521" in text
        assert "lat" in text and "lon" in text

    def test_sync_menampilkan_tunggakan_dan_karantina(self, client: TestClient) -> None:
        text = client.get("/sync").text
        assert "18.400" in text
        assert "dikarantina" in text
        assert "tidak dihapus" in text  # jaminan yang harus terbaca teknisi

    def test_ekspor_menyatakan_data_lokal_tidak_dihapus(self, client: TestClient) -> None:
        assert "tidak dihapus" in client.get("/export").text

    def test_media_tidak_bisa_ditulis_dinonaktifkan(self, client: TestClient) -> None:
        assert "tidak bisa ditulis" in client.get("/export").text

    def test_tanpa_media_memberi_petunjuk(self, make_context: Callable[..., Any]) -> None:
        client = make_client(make_context(empty=True))
        assert "Colokkan USB" in client.get("/export").text

    def test_jaringan_menampilkan_keterjangkauan_central(self, client: TestClient) -> None:
        text = client.get("/network").text
        assert "terjangkau" in text
        assert "10.64.0.12" in text

    def test_log_bisa_disaring(self, client: TestClient) -> None:
        text = client.get("/logs?level=error").text
        assert "InfluxDB menolak" in text
        assert "polling berhasil" not in text


class TestAksesibilitas:
    def test_ada_skip_link(self, client: TestClient) -> None:
        assert 'class="skip"' in client.get("/").text

    def test_status_tidak_hanya_warna(self, client: TestClient) -> None:
        """Setiap chip status membawa simbol. Console dibaca di ruang mesin yang
        terang dan oleh teknisi yang mungkin buta warna."""
        text = client.get("/sensors").text
        assert "●" in text or "◐" in text or "▲" in text

    def test_tabel_punya_caption(self, client: TestClient) -> None:
        assert "<caption" in client.get("/sensors").text

    def test_halaman_aktif_ditandai(self, client: TestClient) -> None:
        assert 'aria-current="page"' in client.get("/sensors").text


class TestRingan:
    def test_hanya_satu_berkas_javascript(self, client: TestClient) -> None:
        assert client.get("/").text.count("<script") == 1

    def test_tidak_ada_sumber_daya_eksternal(self, client: TestClient) -> None:
        """Kapal tidak punya internet. Satu tautan CDN berarti halaman menggantung
        menunggu waktu habis, tepat saat teknisi butuh jawaban cepat."""
        # Yang diperiksa adalah SUMBER DAYA yang diambil — src= dan href= —
        # bukan sembarang string "http://" di halaman. Halaman Log sengaja
        # menampilkan alamat InfluxDB sebagai teks agar teknisi bisa
        # memastikannya; itu informasi, bukan permintaan jaringan.
        diambil = re.compile(r'(?:src|href)\s*=\s*["\'](https?:)?//', re.I)
        for path in PAGES:
            text = client.get(path).text
            cocok = diambil.findall(text)
            assert not cocok, f"{path} memuat sumber daya eksternal: {cocok}"
            assert "//cdn" not in text

    def test_aset_kecil(self, client: TestClient) -> None:
        css = client.get("/static/console.css")
        js = client.get("/static/console.js")
        assert css.status_code == 200 and js.status_code == 200
        assert len(css.content) < 16_000, "CSS membengkak"
        assert len(js.content) < 4_000, "JS membengkak"


class TestRedaksi:
    """Redaksi diterapkan dua kali secara sengaja — oleh penyedia config dan lagi
    oleh Console — sehingga ia harus idempoten."""

    def test_idempoten(self) -> None:
        from fleetview_console.security import NOT_SET, REDACTED, redact_config

        once = redact_config({"influx_token": "rahasia", "device_token": ""})
        twice = redact_config(once)
        assert once == twice
        assert once["influx_token"] == REDACTED
        assert once["device_token"] == NOT_SET

    def test_menangkap_kunci_baru_berdasarkan_pola(self) -> None:
        """Redaksi memakai pola nama, bukan daftar izin. Config bertambah seiring
        waktu, dan kunci baru berisi rahasia harus tertangkap secara default."""
        from fleetview_console.security import REDACTED, redact_config

        out = redact_config(
            {
                "kunci_baru_api_key": "x",
                "server_password": "y",
                "database_dsn": "z",
                "warna_favorit": "hijau",
            }
        )
        assert out["kunci_baru_api_key"] == REDACTED
        assert out["server_password"] == REDACTED
        assert out["database_dsn"] == REDACTED
        assert out["warna_favorit"] == "hijau"

    def test_rekursif_ke_dalam_struktur_bersarang(self) -> None:
        from fleetview_console.security import REDACTED, redact_config

        out = redact_config({"a": {"b": [{"secret": "x"}]}})
        assert out["a"]["b"][0]["secret"] == REDACTED


class TestHalamanSetup:
    """Setup lewat web adalah alasan perangkat baru bisa dipasang tanpa terminal.

    Yang diuji: teknisi tidak perlu mengetik UUID, PIN benar-benar melindungi,
    dan kredensial yang salah tidak menghilangkan apa yang sudah diketik.
    """

    def test_perangkat_belum_dikonfigurasi_dialihkan_ke_setup(
        self, make_context: Callable[..., Any]
    ) -> None:
        client = make_client(make_context(configured=False))
        r = client.get("/", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/setup"

    def test_health_tidak_ikut_dialihkan(self, make_context: Callable[..., Any]) -> None:
        """Pemantau yang menerima HTML akan salah menilai keadaan perangkat."""
        client = make_client(make_context(configured=False))
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_formulir_hanya_meminta_tiga_hal(self, make_context: Callable[..., Any]) -> None:
        """UUID tidak diketik ulang — itu sumber salah ketik yang baru ketahuan
        setelah data masuk atas nama kapal lain."""
        client = make_client(make_context(configured=False))
        html = client.get("/setup").text
        for nama in ("central_url", "client_id", "secret"):
            assert f'name="{nama}"' in html
        assert "ship_id" not in html
        assert "device_id" not in html

    def test_setup_berhasil_meneruskan_masukan(self, make_context: Callable[..., Any]) -> None:
        context = make_context(configured=False)
        client = make_client(context)
        r = client.post(
            "/setup",
            data={
                "central_url": "https://pusat.test",
                "client_id": "ship-071",
                "secret": "rahasia",
                "pin": "123456",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert context.provisioned[0]["client_id"] == "ship-071"
        assert context.provisioned[0]["central_url"] == "https://pusat.test"

    def test_pin_salah_ditolak_tanpa_menyentuh_apa_pun(
        self, make_context: Callable[..., Any]
    ) -> None:
        """Console tidak punya autentikasi lain, dan saat setup ia harus bisa
        dijangkau dari laptop teknisi di jaringan kapal."""
        context = make_context(configured=False)
        client = make_client(context)
        r = client.post(
            "/setup",
            data={
                "central_url": "https://pusat.test",
                "client_id": "ship-071",
                "secret": "rahasia",
                "pin": "000000",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "PIN" in r.headers["location"]
        assert context.provisioned == [], "tidak boleh ada yang ditulis"

    def test_kegagalan_mengembalikan_yang_sudah_diketik_tanpa_rahasia(
        self, make_context: Callable[..., Any]
    ) -> None:
        """Mengetik ulang alamat pusat di tablet sambil berdiri di ruang mesin
        itu menyiksa — tetapi rahasianya tidak boleh ikut ke bilah alamat."""
        context = make_context(configured=False, fail={"provision"})
        client = make_client(context)
        r = client.post(
            "/setup",
            data={
                "central_url": "https://pusat.test",
                "client_id": "ship-071",
                "secret": "rahasia-sekali",
                "pin": "123456",
            },
            follow_redirects=False,
        )
        lokasi = r.headers["location"]
        assert "pusat.test" in lokasi
        assert "ship-071" in lokasi
        assert "rahasia-sekali" not in lokasi

    def test_perangkat_sudah_terpasang_tidak_dialihkan(
        self, make_context: Callable[..., Any]
    ) -> None:
        client = make_client(make_context(configured=True))
        assert client.get("/", follow_redirects=False).status_code == 200


class TestKelolaSensor:
    """Teknisi mendaftarkan sensor dari dashboard, bukan dengan menyunting YAML.

    Ini yang membuat perangkat bisa dipasang tanpa akses terminal — dan yang
    membuat pemetaan ke LP-A104 bisa disiapkan sebelum protokolnya dipastikan.
    """

    def test_daftar_sensor_dan_formulir_muncul(self, make_context: Callable[..., Any]) -> None:
        html = make_client(make_context()).get("/sensors").text
        assert "Daftar sensor terpasang" in html
        assert "Tambah sensor" in html
        assert "me_port_rpm" in html

    def test_menyimpan_sensor_baru(self, make_context: Callable[..., Any]) -> None:
        context = make_context()
        client = make_client(context)
        r = client.post(
            "/sensors/simpan",
            data={
                "sensor_id": "fuel_level_main",
                "channel": "UW120",
                "metric": "level",
                "unit": "percent",
                "scale": "0.1",
                "offset": "0",
                "poll_interval_seconds": "1",
                "min_value": "0",
                "max_value": "100",
                "field_name": "value",
                "enabled": "on",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        tersimpan = next(e for e in context._registry if e["sensor_id"] == "fuel_level_main")
        assert tersimpan["scale"] == 0.1
        assert tersimpan["validation"] == {"min_value": 0.0, "max_value": 100.0}

    def test_koma_desimal_diterima(self, make_context: Callable[..., Any]) -> None:
        """Papan ketik Indonesia lazim memakai koma. Menolaknya membuat teknisi
        buntu pada kolom yang tampak sepele."""
        context = make_context()
        client = make_client(context)
        client.post(
            "/sensors/simpan",
            data={
                "sensor_id": "suhu",
                "channel": "UW130",
                "metric": "temperature",
                "scale": "0,1",
                "offset": "0",
                "poll_interval_seconds": "1",
                "field_name": "value",
                "enabled": "on",
            },
            follow_redirects=False,
        )
        disimpan = next(e for e in context._registry if e["sensor_id"] == "suhu")
        assert disimpan["scale"] == 0.1

    def test_angka_tidak_valid_ditolak_dengan_pesan(self, make_context: Callable[..., Any]) -> None:
        client = make_client(make_context())
        r = client.post(
            "/sensors/simpan",
            data={
                "sensor_id": "suhu",
                "channel": "UW130",
                "metric": "temperature",
                "scale": "dua koma lima",
                "offset": "0",
                "poll_interval_seconds": "1",
                "field_name": "value",
                "enabled": "on",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "error" in r.headers["location"]

    def test_alamat_salah_ditolak_tanpa_menjatuhkan_halaman(
        self, make_context: Callable[..., Any]
    ) -> None:
        """Teknisi mungkin membawa kebiasaan alamat Modbus (40001)."""
        client = make_client(make_context())
        r = client.post(
            "/sensors/simpan",
            data={
                "sensor_id": "suhu",
                "channel": "40001",
                "metric": "temperature",
                "scale": "1",
                "offset": "0",
                "poll_interval_seconds": "1",
                "field_name": "value",
                "enabled": "on",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "error" in r.headers["location"]

    def test_menghapus_sensor(self, make_context: Callable[..., Any]) -> None:
        context = make_context()
        client = make_client(context)
        r = client.post("/sensors/hapus", data={"sensor_id": "me_port_rpm"}, follow_redirects=False)
        assert r.status_code == 303
        assert context._registry == []

    def test_peringatan_lp_a104_ditampilkan(self, make_context: Callable[..., Any]) -> None:
        """Jalur baca belum terkonfirmasi. Teknisi harus tahu bahwa mendaftarkan
        sensor belum berarti datanya akan mengalir."""
        html = make_client(make_context(adapter="lp_a104")).get("/sensors").text
        assert "belum terkonfirmasi" in html

    def test_adapter_lain_tidak_menampilkan_aturan_alamat_lp_a104(
        self, make_context: Callable[..., Any]
    ) -> None:
        html = make_client(make_context(adapter="simulator")).get("/sensors").text
        assert "UW0–UW128999" not in html

    def test_registry_rusak_tidak_menjatuhkan_halaman(
        self, make_context: Callable[..., Any]
    ) -> None:
        html = make_client(make_context(fail={"sensor_registry"})).get("/sensors").text
        assert "Tidak bisa membaca daftar sensor" in html


class TestRincianPenyimpanan:
    """Saat memasang perangkat, pertanyaan pertama teknisi adalah "ini nyimpen ke
    mana?". Menjawabnya lewat berkas config berarti membuka terminal."""

    def test_rincian_influxdb_muncul_di_halaman_log(self, make_context: Callable[..., Any]) -> None:
        html = make_client(make_context()).get("/logs").text
        assert "Penyimpanan lokal" in html
        assert "Bucket" in html
        assert "Organisasi" in html

    def test_token_tidak_pernah_ditampilkan(self, make_context: Callable[..., Any]) -> None:
        """Halaman ini dibuka di kapal, sering di layar yang bisa dilihat orang lain."""
        html = make_client(make_context()).get("/logs").text
        assert "rahasia-token-influx" not in html
        assert "terpasang" in html

    def test_token_kosong_dijelaskan_akibatnya(self, make_context: Callable[..., Any]) -> None:
        html = make_client(make_context(influx_token="")).get("/logs").text
        assert "belum disetel" in html
        assert "ditolak" in html

    def test_kegagalan_tidak_menjatuhkan_halaman_log(
        self, make_context: Callable[..., Any]
    ) -> None:
        html = make_client(make_context(fail={"storage_detail"})).get("/logs").text
        assert "Tidak bisa membaca status penyimpanan" in html
        assert "Log" in html


class TestKataSandiInflux:
    """Username perlu terlihat; kata sandi tidak boleh terbit ke jaringan.

    Console tidak punya autentikasi dan di kapal ia terbuka ke LAN. Kata sandi
    admin InfluxDB memberi kendali penuh atas penyimpanan telemetry kapal,
    termasuk menghapusnya.
    """

    def test_username_ditampilkan(self, make_context: Callable[..., Any]) -> None:
        html = make_client(make_context()).get("/logs").text
        assert "Pengguna" in html
        assert "fleetview" in html

    def test_sandi_tidak_muncul_tanpa_pin(self, make_context: Callable[..., Any]) -> None:
        html = make_client(make_context()).get("/logs").text
        assert "sandi-admin-influx" not in html
        assert "PIN 6 digit" in html

    def test_pin_benar_menampilkan_sandi(self, make_context: Callable[..., Any]) -> None:
        client = make_client(make_context(pin="123456"))
        r = client.post("/logs/sandi", data={"pin": "123456"}, follow_redirects=False)
        assert r.status_code == 303
        assert "sandi=" in r.headers["location"]
        assert "sandi-admin-influx" in client.get(r.headers["location"]).text

    def test_pin_salah_ditolak(self, make_context: Callable[..., Any]) -> None:
        client = make_client(make_context(pin="123456"))
        r = client.post("/logs/sandi", data={"pin": "000000"}, follow_redirects=False)
        assert "sandi_error" in r.headers["location"]
        assert "sandi-admin-influx" not in client.get(r.headers["location"]).text

    def test_sandi_tidak_tersimpan_dijelaskan_cara_menyetelnya(
        self, make_context: Callable[..., Any]
    ) -> None:
        """Perangkat yang InfluxDB-nya disiapkan manual tidak punya berkas itu."""
        html = make_client(make_context(sandi=None)).get("/logs").text
        assert "tidak tersimpan di perangkat ini" in html
        assert "influx user password" in html
