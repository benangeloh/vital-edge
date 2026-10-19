"""Test InfluxTelemetryStore memakai transport HTTP palsu.

Tidak membutuhkan InfluxDB yang berjalan. Yang diuji adalah bagian yang paling
menentukan perilaku di kapal: pemetaan status HTTP ke keputusan "ulangi" atau
"jangan ulangi". Salah memetakan berarti buffer tersumbat selamanya oleh satu
batch rusak, atau data yang baik dibuang percuma.
"""

from __future__ import annotations

from uuid import UUID

import httpx
import pytest

from fleetview_contracts import TelemetryRecord
from fleetview_edge.storage import (
    InfluxTelemetryStore,
    RetentionPolicy,
    StorageRejectedError,
    StorageState,
    StorageUnavailableError,
)
from fleetview_edge.storage.influx import _parse_annotated_csv, _rfc3339_to_micros

SHIP = UUID("11111111-1111-1111-1111-111111111111")
DEVICE = UUID("22222222-2222-2222-2222-222222222222")


def _records(n: int = 1) -> list[TelemetryRecord]:
    return [
        TelemetryRecord(
            ship_id=SHIP,
            device_id=DEVICE,
            sensor_id="me_port_rpm",
            sequence_number=i,
            timestamp=1_756_612_800_000_000 + i,
            metric="rpm",
            values={"value": 742.5},
            unit="rpm",
        )
        for i in range(1, n + 1)
    ]


def _store(handler) -> InfluxTelemetryStore:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://influx.test",
        headers={"Authorization": "Token t"},
    )
    return InfluxTelemetryStore(
        url="http://influx.test",
        org="fleetview",
        bucket="telemetry",
        token="t",
        client=client,
    )


class TestPenulisan:
    async def test_penulisan_berhasil(self) -> None:
        seen: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = request.content.decode()
            seen["params"] = dict(request.url.params)
            return httpx.Response(204)

        await _store(handler).write(_records(2))

        assert seen["params"] == {
            "org": "fleetview",
            "bucket": "telemetry",
            "precision": "us",
        }
        assert len(str(seen["body"]).splitlines()) == 2

    async def test_presisi_mikrodetik_dipakai(self) -> None:
        """Timestamp kita dalam mikrodetik. Presisi yang salah akan menempatkan
        seluruh data ribuan tahun dari sekarang, tanpa error apa pun."""
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["precision"] = request.url.params["precision"]
            return httpx.Response(204)

        await _store(handler).write(_records())
        assert seen["precision"] == "us"

    async def test_daftar_kosong_tidak_memanggil_http(self) -> None:
        called = False

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal called
            called = True
            return httpx.Response(204)

        await _store(handler).write([])
        assert called is False

    async def test_token_dikirim_di_header(self) -> None:
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["auth"] = request.headers.get("Authorization", "")
            return httpx.Response(204)

        await _store(handler).write(_records())
        assert seen["auth"] == "Token t"


class TestPemetaanKegagalan:
    """4xx berarti jangan diulang, 5xx dan 429 berarti boleh — aturan yang sama
    dengan protokol sync di docs/architecture/04-api-boundaries.md."""

    @pytest.mark.parametrize("status", [500, 502, 503, 504])
    async def test_5xx_bisa_diulang(self, status: int) -> None:
        store = _store(lambda _r: httpx.Response(status, text="boom"))
        with pytest.raises(StorageUnavailableError) as exc:
            await store.write(_records())
        assert exc.value.retryable is True

    async def test_429_bisa_diulang_dan_membawa_retry_after(self) -> None:
        store = _store(
            lambda _r: httpx.Response(429, headers={"Retry-After": "30"}, text="slow down")
        )
        with pytest.raises(StorageUnavailableError) as exc:
            await store.write(_records())
        assert exc.value.retryable is True
        assert exc.value.details["retry_after"] == "30"

    @pytest.mark.parametrize("status", [400, 413, 422])
    async def test_payload_buruk_tidak_diulang(self, status: int) -> None:
        """Payload salah bentuk tidak akan pernah diterima berapa kali pun
        dicoba, dan satu batch rusak akan menyumbat buffer kalau kita coba."""
        store = _store(lambda _r: httpx.Response(status, text="nope"))
        with pytest.raises(StorageRejectedError) as exc:
            await store.write(_records())
        assert exc.value.retryable is False

    async def test_jaringan_putus_bisa_diulang(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        with pytest.raises(StorageUnavailableError) as exc:
            await _store(handler).write(_records())
        assert exc.value.retryable is True

    async def test_timeout_bisa_diulang(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("kelamaan")

        with pytest.raises(StorageUnavailableError):
            await _store(handler).write(_records())


class TestKesehatan:
    async def test_sehat_saat_health_menjawab_200(self) -> None:
        store = _store(lambda _r: httpx.Response(200, json={"status": "pass"}))
        health = await store.health()
        assert health.state is StorageState.HEALTHY
        assert health.reachable is True

    async def test_tidak_tersedia_saat_tidak_bisa_dijangkau(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        health = await _store(handler).health()
        assert health.state is StorageState.UNAVAILABLE
        assert health.reachable is False
        assert health.detail is not None

    async def test_degraded_setelah_penulisan_gagal_meski_masih_hidup(self) -> None:
        """Bisa dijangkau tapi menolak penulisan — kondisi yang paling
        membingungkan saat troubleshooting, jadi harus punya nama sendiri."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/health":
                return httpx.Response(200, json={"status": "pass"})
            return httpx.Response(500, text="boom")

        store = _store(handler)
        with pytest.raises(StorageUnavailableError):
            await store.write(_records())

        health = await store.health()
        assert health.state is StorageState.DEGRADED
        assert health.consecutive_failures == 1

    async def test_health_tidak_melempar_saat_database_mati(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("mati total")

        health = await _store(handler).health()  # tidak boleh melempar
        assert health.healthy is False


class TestRetensi:
    def test_hari_jadi_detik(self) -> None:
        assert RetentionPolicy(days=90).seconds == 90 * 86_400

    def test_nol_berarti_tak_terbatas(self) -> None:
        p = RetentionPolicy(days=0)
        assert p.unlimited is True
        assert p.to_bucket_rules() == []

    def test_aturan_bucket(self) -> None:
        assert RetentionPolicy(days=30).to_bucket_rules() == [
            {"type": "expire", "everySeconds": 2_592_000}
        ]

    def test_shard_duration_disertakan_bila_disetel(self) -> None:
        rules = RetentionPolicy(days=30, shard_group_duration_hours=6).to_bucket_rules()
        assert rules[0]["shardGroupDurationSeconds"] == 21_600

    def test_retensi_negatif_ditolak(self) -> None:
        with pytest.raises(ValueError, match="negatif"):
            RetentionPolicy(days=-1)


class TestEnsureReady:
    async def test_bucket_yang_sudah_ada_dibiarkan(self) -> None:
        posted = False

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal posted
            if request.method == "POST":
                posted = True
            return httpx.Response(200, json={"buckets": [{"id": "abc"}]})

        await _store(handler).ensure_ready()
        assert posted is False

    async def test_bucket_dibuat_dengan_retensi(self) -> None:
        created: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and request.url.path == "/api/v2/buckets":
                return httpx.Response(200, json={"buckets": []})
            if request.url.path == "/api/v2/orgs":
                return httpx.Response(200, json={"orgs": [{"id": "org-1"}]})
            import json as _json

            created.update(_json.loads(request.content))
            return httpx.Response(201, json={"id": "new"})

        store = InfluxTelemetryStore(
            url="http://influx.test",
            org="fleetview",
            bucket="telemetry",
            token="t",
            retention=RetentionPolicy(days=45),
            client=httpx.AsyncClient(
                transport=httpx.MockTransport(handler), base_url="http://influx.test"
            ),
        )
        await store.ensure_ready()

        assert created["name"] == "telemetry"
        assert created["retentionRules"] == [{"type": "expire", "everySeconds": 45 * 86_400}]

    async def test_database_mati_tidak_menghentikan_agent(self) -> None:
        """Kapal tetap harus mengumpulkan data meski penyiapan database gagal."""

        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("mati")

        await _store(handler).ensure_ready()  # tidak boleh melempar


_CSV_DUA_TITIK = (
    "#datatype,string,long,dateTime:RFC3339,dateTime:RFC3339,dateTime:RFC3339,"
    "double,string,string,string\n"
    "#group,false,false,true,true,false,false,true,true,true\n"
    "#default,_result,,,,,,,,\n"
    ",result,table,_start,_stop,_time,_value,_field,_measurement,sensor_id\n"
    ",,0,2024-01-01T00:00:00Z,2024-01-01T01:00:00Z,"
    "2024-01-01T00:59:58.123456789Z,742.5,value,rpm,me_port_rpm\n"
    ",,0,2024-01-01T00:00:00Z,2024-01-01T01:00:00Z,"
    "2024-01-01T00:59:57.000000000Z,741.0,value,rpm,me_port_rpm\n"
)


class TestPratinjau:
    """Fitur ini ada supaya teknisi tidak perlu membuka InfluxDB langsung di
    :8086 hanya untuk memastikan datanya benar-benar tersimpan."""

    async def test_query_flux_terkirim_dan_csv_terurai(self) -> None:
        seen: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = request.content.decode()
            seen["content_type"] = request.headers.get("content-type")
            seen["accept"] = request.headers.get("accept")
            return httpx.Response(200, text=_CSV_DUA_TITIK)

        rows = await _store(handler).query_recent(limit=20)

        assert seen["content_type"] == "application/vnd.flux"
        assert seen["accept"] == "application/csv"
        assert 'from(bucket: "telemetry")' in str(seen["body"])

        assert len(rows) == 2
        assert rows[0]["sensor_id"] == "me_port_rpm"
        assert rows[0]["metric"] == "rpm"
        assert rows[0]["field"] == "value"
        assert rows[0]["nilai"] == "742.5"
        # Terurut terbaru dulu, bukan urutan CSV apa adanya.
        assert rows[0]["waktu_us"] > rows[1]["waktu_us"]

    async def test_limit_dipotong_di_python(self) -> None:
        """Flux sudah diminta membatasi, tetapi Python memotong lagi setelah
        pengurutan ulang — lihat catatan di docstring `query_recent`."""
        rows = await _store(lambda _r: httpx.Response(200, text=_CSV_DUA_TITIK)).query_recent(
            limit=1
        )
        assert len(rows) == 1
        assert rows[0]["sensor_id"] == "me_port_rpm"

    async def test_tidak_terjangkau_dilaporkan_bisa_diulang(self) -> None:
        def handler(_r: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        with pytest.raises(StorageUnavailableError):
            await _store(handler).query_recent()

    async def test_query_ditolak_tidak_diulang(self) -> None:
        store = _store(lambda _r: httpx.Response(400, text="invalid flux syntax"))
        with pytest.raises(StorageRejectedError):
            await store.query_recent()

    def test_urai_csv_menangani_banyak_tabel(self) -> None:
        """`group()` tidak selalu bisa menyatukan measurement bertipe field
        berbeda menjadi satu tabel — CSV-nya lantas berisi header berulang,
        dipisah baris kosong."""
        csv = (
            ",a,b\n"
            ",1,2\n"
            "\n"
            ",x,y,z\n"
            ",7,8,9\n"
        )
        rows = _parse_annotated_csv(csv)
        assert rows == [{"": "", "a": "1", "b": "2"}, {"": "", "x": "7", "y": "8", "z": "9"}]

    def test_rfc3339_presisi_nano_dipotong_ke_mikro(self) -> None:
        assert _rfc3339_to_micros("2024-01-01T00:59:58.123456789Z") == _rfc3339_to_micros(
            "2024-01-01T00:59:58.123456Z"
        )

    def test_rfc3339_tanpa_akhiran_z_ditolak(self) -> None:
        assert _rfc3339_to_micros("2024-01-01T00:59:58+07:00") is None

    def test_rfc3339_kosong_ditolak(self) -> None:
        assert _rfc3339_to_micros("") is None


class TestKegagalanKonfigurasi:
    """Token salah adalah salah konfigurasi, bukan data yang buruk.

    Ditemukan di Raspberry Pi sungguhan: token InfluxDB belum terpasang, dan
    agent membuang 1.368 pembacaan secara permanen sebelum ada yang memeriksa
    log. Begitu token diperbaiki, percobaan ulang berhasil — jadi membuangnya
    adalah kehilangan yang sama sekali tidak perlu.
    """

    @pytest.mark.parametrize("status", [401, 403, 404])
    async def test_kesalahan_konfigurasi_bisa_dicoba_ulang(self, status: int) -> None:
        store = _store(lambda _r: httpx.Response(status, text="unauthorized access"))
        with pytest.raises(StorageUnavailableError) as exc:
            await store.write(_records())
        assert exc.value.retryable is True

    async def test_token_diperbaiki_lalu_penulisan_berhasil(self) -> None:
        """Inti perbaikan ini: kegagalan konfigurasi bersifat sementara bila
        dilihat dari sudut pandang datanya."""
        keadaan = {"token_benar": False}

        def handler(_r: httpx.Request) -> httpx.Response:
            if not keadaan["token_benar"]:
                return httpx.Response(401, text="unauthorized access")
            return httpx.Response(204)

        store = _store(handler)
        with pytest.raises(StorageUnavailableError):
            await store.write(_records())

        keadaan["token_benar"] = True  # operator memperbaiki token
        await store.write(_records())  # tidak melempar lagi
