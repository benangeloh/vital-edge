"""Test Edge Console.

Dua jaminan yang paling menentukan diuji di sini, dan keduanya tentang keadaan
buruk — karena itulah saat Console dibuka:

1. **Halaman tetap tampil ketika komponennya rusak.** Collector mati harus
   menghasilkan satu kartu berisi pesan error, bukan halaman yang gagal dimuat.
2. **Rahasia tidak pernah sampai ke layar.**
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from fleetview_console import create_console_app

from .fakes import FakeContext

PAGES = ["/", "/sensors", "/sync", "/export", "/network", "/logs", "/config"]


def make_client(context: FakeContext | None = None) -> TestClient:
    app = create_console_app(
        context=context or FakeContext(),
        ship_name="KM Sinar Jaya",
        ship_id="11111111-1111-1111-1111-111111111111",
        agent_version="0.1.0",
        environment="production",
    )
    return TestClient(app)


@pytest.fixture
def client() -> TestClient:
    return make_client()


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
    def test_halaman_tetap_200_dan_menjelaskan(self, path: str, broken: str) -> None:
        client = make_client(FakeContext(fail={broken}))
        response = client.get(path)
        assert response.status_code == 200
        assert "tidak tersedia" in response.text

    def test_ikhtisar_tetap_menampilkan_sync_saat_sistem_rusak(self) -> None:
        """Bagian yang masih bisa dibaca harus tetap terbaca — itu inti
        pengambilan data per bagian."""
        client = make_client(FakeContext(fail={"system"}))
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

    def test_trigger_sync_memanggil_agent_lalu_mengalihkan(self) -> None:
        context = FakeContext()
        client = make_client(context)
        response = client.post("/sync/trigger", follow_redirects=False)
        assert response.status_code == 303
        assert context.sync_triggered == 1

    def test_trigger_gagal_tetap_mengalihkan_dengan_pesan(self) -> None:
        client = make_client(FakeContext(fail={"trigger_sync"}))
        response = client.post("/sync/trigger", follow_redirects=False)
        assert response.status_code == 303
        assert "error=1" in response.headers["location"]

    def test_ekspor_memanggil_agent(self) -> None:
        context = FakeContext()
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

    def test_tanpa_media_memberi_petunjuk(self) -> None:
        client = make_client(FakeContext(empty=True))
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
        for path in PAGES:
            text = client.get(path).text
            assert "http://" not in text.replace("http://localhost", "")
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
