"""Test NetworkTransport dan FileExportTransport.

Keduanya memenuhi kontrak TransportAdapter yang sama — itulah inti Phase 4.
Yang diuji di sini adalah perilaku spesifik masing-masing: resume berpotongan
di jaringan, dan paket portabel di USB.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import httpx
import pytest

from fleetview_common import now_utc
from fleetview_contracts import SCHEMA_VERSION, BatchEnvelope
from fleetview_edge.sync import (
    FileExportTransport,
    NetworkTransport,
    TransportKind,
    TransportRejectedError,
    TransportUnavailableError,
)
from fleetview_edge.sync.fileexport import MANIFEST_NAME, TELEMETRY_DIR

SHIP = UUID("11111111-1111-1111-1111-111111111111")
DEVICE = UUID("22222222-2222-2222-2222-222222222222")
BATCH = UUID("018f2c00-0000-7000-8000-000000000001")


def _envelope(size: int = 100, checksum: str | None = None) -> BatchEnvelope:
    return BatchEnvelope(
        schema_version=SCHEMA_VERSION,
        batch_id=BATCH,
        ship_id=SHIP,
        device_id=DEVICE,
        sequence_start=1,
        sequence_end=5,
        first_timestamp=now_utc(),
        last_timestamp=now_utc(),
        record_count=5,
        payload_checksum=checksum or ("sha256:" + "a" * 64),
        payload_size=size,
        agent_version="0.1.0",
        config_version="cfg-1",
        created_at=now_utc(),
    )


def _ack_body(batch_id: UUID = BATCH) -> dict[str, object]:
    return {
        "batch_id": str(batch_id),
        "status": "committed",
        "last_contiguous_sequence": 5,
        "server_received_at": now_utc().isoformat(),
        "record_count": 5,
    }


def _transport(handler, **kw) -> NetworkTransport:
    return NetworkTransport(
        base_url="http://central.test",
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://central.test"
        ),
        **kw,
    )


class TestNetworkSingleShot:
    async def test_batch_kecil_dikirim_sekali_jalan(self) -> None:
        """Membuka sesi berpotongan untuk payload 20 KB hanya menambah tiga
        round trip tanpa manfaat."""
        paths: list[str] = []

        def handler(r: httpx.Request) -> httpx.Response:
            paths.append(r.url.path)
            return httpx.Response(200, json=_ack_body())

        result = await _transport(handler).send(_envelope(), b"x" * 100)
        assert result.ack is not None
        assert paths == ["/api/v1/ingest/batches"]

    async def test_ack_diverifikasi_terhadap_batch_yang_dikirim(self) -> None:
        def handler(_r: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_ack_body(UUID(int=999)))

        with pytest.raises(TransportRejectedError, match="merujuk batch lain"):
            await _transport(handler).send(_envelope(), b"x" * 100)


class TestNetworkResume:
    async def test_payload_besar_dikirim_berpotongan(self) -> None:
        chunks: list[int] = []

        def handler(r: httpx.Request) -> httpx.Response:
            if r.url.path.endswith("/sessions"):
                return httpx.Response(
                    201, json={"session_id": "s1", "chunk_size": 1024, "received_chunks": []}
                )
            if "/chunks/" in r.url.path:
                chunks.append(int(r.url.path.rsplit("/", 1)[1]))
                return httpx.Response(204)
            return httpx.Response(200, json=_ack_body())

        payload = b"x" * (1024 * 5)
        result = await _transport(handler, single_shot_limit=0).send(
            _envelope(len(payload)), payload
        )

        assert result.ack is not None
        assert chunks == [0, 1, 2, 3, 4]

    async def test_melanjutkan_hanya_mengirim_potongan_yang_kurang(self) -> None:
        """Inti kemampuan resume: setelah putus, hanya sisanya yang dikirim.
        Tanpa ini, backlog 30 hari tidak akan pernah selesai terkirim."""
        chunks: list[int] = []

        def handler(r: httpx.Request) -> httpx.Response:
            if r.url.path.endswith("/sessions"):
                return httpx.Response(
                    201, json={"session_id": "s1", "chunk_size": 1024, "received_chunks": [0, 1, 2]}
                )
            if "/chunks/" in r.url.path:
                chunks.append(int(r.url.path.rsplit("/", 1)[1]))
                return httpx.Response(204)
            return httpx.Response(200, json=_ack_body())

        payload = b"x" * (1024 * 5)
        result = await _transport(handler, single_shot_limit=0).send(
            _envelope(len(payload)), payload
        )

        assert chunks == [3, 4]  # hanya yang kurang
        assert result.resumed_from_chunk == 3
        assert result.bytes_sent == 2048  # bukan 5120

    async def test_batch_yang_sudah_committed_tidak_dikirim_ulang(self) -> None:
        """ACK yang hilang berbiaya satu round trip, bukan satu upload ulang."""
        sent_chunks = 0

        def handler(r: httpx.Request) -> httpx.Response:
            nonlocal sent_chunks
            if r.url.path.endswith("/sessions"):
                return httpx.Response(200, json={"status": "already_committed", "ack": _ack_body()})
            sent_chunks += 1
            return httpx.Response(204)

        payload = b"x" * (1024 * 5)
        result = await _transport(handler, single_shot_limit=0).send(
            _envelope(len(payload)), payload
        )

        assert result.ack is not None
        assert result.bytes_sent == 0
        assert sent_chunks == 0

    async def test_checksum_dikirim_saat_commit(self) -> None:
        committed: dict[str, object] = {}

        def handler(r: httpx.Request) -> httpx.Response:
            if r.url.path.endswith("/sessions"):
                return httpx.Response(
                    201, json={"session_id": "s1", "chunk_size": 4096, "received_chunks": []}
                )
            if r.url.path.endswith("/commit"):
                committed.update(json.loads(r.content))
                return httpx.Response(200, json=_ack_body())
            return httpx.Response(204)

        payload = b"x" * (1024 * 5)
        env = _envelope(len(payload), checksum="sha256:" + "b" * 64)
        await _transport(handler, single_shot_limit=0).send(env, payload)
        assert committed["payload_checksum"] == env.payload_checksum


class TestNetworkKegagalan:
    @pytest.mark.parametrize("status", [500, 503])
    async def test_5xx_bisa_diulang(self, status: int) -> None:
        with pytest.raises(TransportUnavailableError):
            await _transport(lambda _r: httpx.Response(status)).send(_envelope(), b"x")

    async def test_429_bisa_diulang(self) -> None:
        """Respons yang memang diharapkan saat 70 kapal tersambung bersamaan."""
        with pytest.raises(TransportUnavailableError):
            await _transport(lambda _r: httpx.Response(429)).send(_envelope(), b"x")

    @pytest.mark.parametrize("status", [400, 401, 409, 422])
    async def test_4xx_tidak_diulang(self, status: int) -> None:
        with pytest.raises(TransportRejectedError):
            await _transport(lambda _r: httpx.Response(status)).send(_envelope(), b"x")

    async def test_koneksi_putus_bisa_diulang(self) -> None:
        def handler(_r: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("putus")

        with pytest.raises(TransportUnavailableError):
            await _transport(handler).send(_envelope(), b"x")

    async def test_is_available_probe_sungguhan(self) -> None:
        """Wi-Fi kapal yang associate tapi tidak punya rute adalah kasus paling
        umum, dan pemeriksaan tingkat antarmuka melaporkannya sebagai sehat."""
        assert await _transport(lambda _r: httpx.Response(200)).is_available() is True

        def down(_r: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("tidak ada rute")

        assert await _transport(down).is_available() is False


class TestFileExport:
    async def test_struktur_paket(self, tmp_path: Path) -> None:
        t = FileExportTransport(target_dir=tmp_path, ship_slug="SHIP-023")
        await t.send(_envelope(4), b"data")

        root = tmp_path / "SHIP-023"
        assert (root / MANIFEST_NAME).is_file()
        assert (root / TELEMETRY_DIR).is_dir()
        assert (root / "checksums").is_dir()
        assert len(list((root / TELEMETRY_DIR).glob("*.fvb"))) == 1

    async def test_manifest_memuat_field_yang_disyaratkan(self, tmp_path: Path) -> None:
        t = FileExportTransport(target_dir=tmp_path, ship_slug="SHIP-023")
        await t.send(_envelope(4), b"data")

        manifest = json.loads((tmp_path / "SHIP-023" / MANIFEST_NAME).read_text())
        for key in (
            "ship_id",
            "device_id",
            "schema_version",
            "first_timestamp",
            "last_timestamp",
            "record_count",
            "export_checksum",
        ):
            assert key in manifest, f"manifest kurang {key}"
        assert manifest["ship_id"] == str(SHIP)
        assert manifest["record_count"] == 5

    async def test_export_tidak_menghasilkan_ack(self, tmp_path: Path) -> None:
        """Berkas sudah tertulis, tetapi tidak ada yang mengonfirmasi central
        menerimanya. Sync Engine karena itu tidak menandainya synchronized."""
        t = FileExportTransport(target_dir=tmp_path, ship_slug="SHIP-023")
        result = await t.send(_envelope(4), b"data")
        assert result.ack is None

    async def test_byte_identik_dengan_yang_dikirim_http(self, tmp_path: Path) -> None:
        """Bukan format kedua. Alat impor mem-POST berkas ini ke endpoint ingest
        yang sama persis."""
        payload = b"payload-terkompresi-apa-adanya"
        t = FileExportTransport(target_dir=tmp_path, ship_slug="SHIP-023")
        await t.send(_envelope(len(payload)), payload)

        written = next((tmp_path / "SHIP-023" / TELEMETRY_DIR).glob("*.fvb"))
        assert written.read_bytes() == payload

    async def test_beberapa_batch_menumpuk_di_manifest(self, tmp_path: Path) -> None:
        t = FileExportTransport(target_dir=tmp_path, ship_slug="SHIP-023")
        await t.send(_envelope(4), b"aaaa")
        env2 = _envelope(4).model_copy(
            update={
                "batch_id": UUID(int=7),
                "record_count": 3,
                "sequence_start": 6,
                "sequence_end": 8,
            }
        )
        await t.send(env2, b"bbbb")

        manifest = json.loads((tmp_path / "SHIP-023" / MANIFEST_NAME).read_text())
        assert len(manifest["batches"]) == 2
        assert manifest["record_count"] == 8

    async def test_verifikasi_paket_valid(self, tmp_path: Path) -> None:
        from fleetview_edge.outbox import checksum_of

        payload = b"data"
        t = FileExportTransport(target_dir=tmp_path, ship_slug="SHIP-023")
        await t.send(_envelope(len(payload), checksum=checksum_of(payload)), payload)

        ok, problems = FileExportTransport.verify_package(tmp_path / "SHIP-023")
        assert ok, problems

    async def test_verifikasi_menangkap_checksum_tidak_cocok(self, tmp_path: Path) -> None:
        """Media rusak harus ketahuan sebelum kita mempercayai satu byte pun."""
        from fleetview_edge.outbox import checksum_of

        payload = b"data"
        t = FileExportTransport(target_dir=tmp_path, ship_slug="SHIP-023")
        await t.send(_envelope(len(payload), checksum=checksum_of(payload)), payload)

        written = next((tmp_path / "SHIP-023" / TELEMETRY_DIR).glob("*.fvb"))
        written.write_bytes(b"rusak")

        ok, problems = FileExportTransport.verify_package(tmp_path / "SHIP-023")
        assert ok is False
        assert any("checksum tidak cocok" in p for p in problems)

    async def test_verifikasi_menangkap_berkas_hilang(self, tmp_path: Path) -> None:
        t = FileExportTransport(target_dir=tmp_path, ship_slug="SHIP-023")
        await t.send(_envelope(4), b"data")
        next((tmp_path / "SHIP-023" / TELEMETRY_DIR).glob("*.fvb")).unlink()

        ok, problems = FileExportTransport.verify_package(tmp_path / "SHIP-023")
        assert ok is False
        assert any("hilang" in p for p in problems)

    async def test_media_tidak_terpasang_ditolak(self, tmp_path: Path) -> None:
        t = FileExportTransport(target_dir=tmp_path / "tidak-ada", ship_slug="SHIP-023")
        assert await t.is_available() is False
        with pytest.raises(TransportUnavailableError):
            await t.send(_envelope(4), b"data")

    async def test_jenis_transport_usb(self, tmp_path: Path) -> None:
        t = FileExportTransport(target_dir=tmp_path, ship_slug="SHIP-023")
        assert t.kind is TransportKind.USB


class TestPenukaranKredensial:
    """Agent menukar kredensial jangka panjang menjadi token, dan menukar ulang
    saat token kedaluwarsa.

    Tanpa ini kapal hanya bisa menyetor data selama masa berlaku token — default
    satu jam — lalu berhenti sampai ada orang yang mengganti konfigurasinya.
    """

    def _handler(self, sequence: list[httpx.Response], seen: list[httpx.Request]):
        def handler(r: httpx.Request) -> httpx.Response:
            seen.append(r)
            if r.url.path == "/api/v1/auth/device/token":
                return httpx.Response(200, json={"data": {"access_token": "token-baru"}})
            return sequence.pop(0) if sequence else httpx.Response(200, json=_ack_body())

        return handler

    async def test_menukar_kredensial_sebelum_permintaan_pertama(self) -> None:
        seen: list[httpx.Request] = []
        transport = _transport(self._handler([], seen), client_id="ship-023", secret="rahasia")
        await transport.send(_envelope(), b"x" * 100)

        assert seen[0].url.path == "/api/v1/auth/device/token"
        assert seen[1].headers["Authorization"] == "Bearer token-baru"

    async def test_token_kedaluwarsa_ditukar_lalu_permintaan_diulang(self) -> None:
        seen: list[httpx.Request] = []
        transport = _transport(
            self._handler([httpx.Response(401, json={"error": "kedaluwarsa"})], seen),
            token="token-lama",
            client_id="ship-023",
            secret="rahasia",
        )
        result = await transport.send(_envelope(), b"x" * 100)

        assert result.ack is not None, "batch harus berhasil setelah token diperbarui"
        paths = [r.url.path for r in seen]
        assert paths == [
            "/api/v1/ingest/batches",  # dengan token lama -> 401
            "/api/v1/auth/device/token",  # tukar
            "/api/v1/ingest/batches",  # diulang dengan token baru
        ]

    async def test_401_terus_menerus_tidak_menukar_berulang(self) -> None:
        """Kalau permintaan kedua tetap 401 dengan token yang baru diterbitkan,
        masalahnya bukan kedaluwarsa. Mengulang terus hanya menabrak rate limit
        auth di central."""
        seen: list[httpx.Request] = []

        def handler(r: httpx.Request) -> httpx.Response:
            seen.append(r)
            if r.url.path == "/api/v1/auth/device/token":
                return httpx.Response(200, json={"data": {"access_token": "token-baru"}})
            return httpx.Response(401, json={"error": "tetap ditolak"})

        transport = _transport(handler, token="lama", client_id="ship-023", secret="rahasia")
        with pytest.raises(TransportRejectedError):
            await transport.send(_envelope(), b"x" * 100)

        tukar = [r for r in seen if r.url.path == "/api/v1/auth/device/token"]
        assert len(tukar) == 1, "penukaran hanya boleh sekali per permintaan"

    async def test_kredensial_dicabut_ditolak_bukan_dicoba_ulang(self) -> None:
        """Kredensial yang dicabut TIDAK bisa diperbaiki dengan mencoba ulang.
        Menandainya retryable akan membuat kapal membombardir endpoint auth
        selamanya."""

        def handler(r: httpx.Request) -> httpx.Response:
            if r.url.path == "/api/v1/auth/device/token":
                return httpx.Response(401, json={"error": "dicabut"})
            return httpx.Response(200, json=_ack_body())

        transport = _transport(handler, client_id="ship-023", secret="sudah-dicabut")
        with pytest.raises(TransportRejectedError, match="dicabut atau salah disalin"):
            await transport.send(_envelope(), b"x" * 100)

    async def test_central_mati_saat_menukar_bukan_alasan_karantina(self) -> None:
        """Central yang sedang mati tidak boleh membuat batch masuk karantina
        hanya karena kebetulan tokennya perlu diperbarui saat itu."""

        def handler(r: httpx.Request) -> httpx.Response:
            if r.url.path == "/api/v1/auth/device/token":
                return httpx.Response(503, text="sedang dipelihara")
            return httpx.Response(200, json=_ack_body())

        transport = _transport(handler, client_id="ship-023", secret="rahasia")
        with pytest.raises(TransportUnavailableError):
            await transport.send(_envelope(), b"x" * 100)

    async def test_tanpa_kredensial_401_tidak_memicu_penukaran(self) -> None:
        """Konfigurasi hanya dengan token statis harus tetap berperilaku seperti
        sebelumnya: 401 ditolak, bukan memicu penukaran yang mustahil."""
        seen: list[httpx.Request] = []

        def handler(r: httpx.Request) -> httpx.Response:
            seen.append(r)
            return httpx.Response(401, json={"error": "tidak sah"})

        with pytest.raises(TransportRejectedError):
            await _transport(handler, token="statis").send(_envelope(), b"x" * 100)
        assert all(r.url.path != "/api/v1/auth/device/token" for r in seen)
