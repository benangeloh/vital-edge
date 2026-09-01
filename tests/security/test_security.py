"""Pemeriksaan keamanan.

Bukan pengganti audit keamanan. Yang diperiksa di sini adalah properti yang
harus dijaga oleh test otomatis karena mudah rusak diam-diam saat kode berubah:
pemisahan token, penegakan peran, redaksi rahasia, dan pesan error yang tidak
membocorkan isi sistem.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text

from fleetview_central.main import create_app
from fleetview_central.modules.fleet.models import Device, Ship
from fleetview_central.modules.identity.service import IdentityService
from fleetview_central.platform.db import Base, Database
from fleetview_central.platform.influx import InfluxGateway
from fleetview_central.platform.security import TokenService, hash_secret, verify_secret
from fleetview_central.settings import ApiSettings
from fleetview_console.security import redact_config

pytestmark = [pytest.mark.security, pytest.mark.integration]

POSTGRES_DSN = os.getenv(
    "FLEETVIEW_TEST_POSTGRES_DSN",
    "postgresql+asyncpg://fleetview:fleetview@localhost:5432/fleetview",
)
INFLUX_URL = os.getenv("FLEETVIEW_TEST_INFLUX_URL", "http://localhost:8086")
INFLUX_TOKEN = os.getenv("FLEETVIEW_TEST_INFLUX_TOKEN", "fleetview-dev-token")

PASSWORD = "kata-sandi-uji-yang-panjang"

_up: bool | None = None


@dataclass
class Stack:
    """Tumpukan uji: client HTTP, database, dan penerbit token.

    Dataclass, bukan dict: setiap test mengaksesnya dan tipe yang jelas
    menghilangkan puluhan cast di berkas ini.
    """

    client: httpx.AsyncClient
    db: Database
    tokens: TokenService
    ship_id: UUID
    device_id: UUID


async def _services_up() -> bool:
    try:
        db = Database(POSTGRES_DSN)
        async with db.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        await db.dispose()
        async with httpx.AsyncClient(timeout=3.0) as probe:
            response = await probe.get(f"{INFLUX_URL}/health")
        return bool(response.status_code == 200)
    except Exception:
        return False


@pytest_asyncio.fixture
async def stack() -> AsyncIterator[Stack]:
    global _up
    if _up is None:
        _up = await _services_up()
    if not _up:
        pytest.skip("Postgres/InfluxDB tidak berjalan; jalankan 'make up'")

    db = Database(POSTGRES_DSN)
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    influx = InfluxGateway(url=INFLUX_URL, org="fleetview", bucket="telemetry", token=INFLUX_TOKEN)
    tokens = TokenService("rahasia-uji-keamanan-yang-cukup-panjang", ttl_seconds=3600)
    app = create_app(
        ApiSettings(
            environment="development",
            log_format="console",
            postgres_dsn=POSTGRES_DSN,
            influx_url=INFLUX_URL,
            influx_token=INFLUX_TOKEN,
        )
    )
    app.state.database = db
    app.state.influx = influx
    app.state.tokens = tokens

    ship_id, device_id = uuid4(), uuid4()
    async with db.session() as session:
        session.add(Ship(id=ship_id, name="KM Uji", slug="SHIP-SEC"))
        await session.flush()
        # Device di-flush setelah Ship: kredensial device punya foreign key ke
        # keduanya, dan urutan insert dalam satu flush tidak dijamin.
        session.add(Device(id=device_id, ship_id=ship_id, name="Edge Pi"))
        await session.flush()
        identity = IdentityService(session, tokens)
        await identity.create_user(username="admin", password=PASSWORD, roles=["admin"])
        await identity.create_user(username="pengamat", password=PASSWORD)
        await identity.create_device_credential(
            device_id=device_id,
            ship_id=ship_id,
            client_id="dev-sec",
            secret="rahasia-device-yang-panjang",
        )
        await session.commit()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://sec.test"
    ) as client:
        yield Stack(
            client=client,
            db=db,
            tokens=tokens,
            ship_id=ship_id,
            device_id=device_id,
        )
    await influx.close()
    await db.dispose()


async def _login(client: httpx.AsyncClient, username: str) -> str:
    response = await client.post(
        "/api/v1/auth/login", json={"username": username, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    return str(response.json()["data"]["access_token"])


async def _device_token(client: httpx.AsyncClient) -> str:
    response = await client.post(
        "/api/v1/auth/device/token",
        json={"client_id": "dev-sec", "secret": "rahasia-device-yang-panjang"},
    )
    return str(response.json()["data"]["access_token"])


class TestAutentikasi:
    async def test_endpoint_tanpa_token_ditolak(self, stack: Stack) -> None:
        for path in (
            "/api/v1/ships",
            "/api/v1/devices",
            "/api/v1/audit",
            "/api/v1/ingest/sync-state",
        ):
            assert (await stack.client.get(path)).status_code == 401, path

    async def test_token_palsu_ditolak(self, stack: Stack) -> None:
        for bad in ("bukan-token", "Bearer", "eyJ.palsu.tanda-tangan"):
            response = await stack.client.get(
                "/api/v1/ships", headers={"Authorization": f"Bearer {bad}"}
            )
            assert response.status_code == 401

    async def test_token_ditandatangani_kunci_lain_ditolak(self, stack: Stack) -> None:
        """Kalau verifikasi tanda tangan longgar, siapa pun bisa menerbitkan
        token sendiri."""
        penyerang = TokenService("kunci-penyerang-yang-juga-cukup-panjang")
        token = penyerang.issue_user_token(uuid4(), "admin", {"admin"})
        response = await stack.client.get(
            "/api/v1/ships", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 401

    async def test_token_kedaluwarsa_ditolak(self, stack: Stack) -> None:
        expired = TokenService(
            "rahasia-uji-keamanan-yang-cukup-panjang", ttl_seconds=-10
        ).issue_user_token(uuid4(), "admin", {"admin"})
        response = await stack.client.get(
            "/api/v1/ships", headers={"Authorization": f"Bearer {expired}"}
        )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "auth.token_expired"

    async def test_pesan_gagal_login_tidak_membocorkan_username_terdaftar(
        self, stack: Stack
    ) -> None:
        """Pesan berbeda untuk 'user tidak ada' dan 'sandi salah' cukup untuk
        menyusun daftar akun yang terdaftar sebelum menebak sandinya."""
        ada = await stack.client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "salah"}
        )
        tidak_ada = await stack.client.post(
            "/api/v1/auth/login", json={"username": "hantu", "password": "salah"}
        )
        assert ada.status_code == tidak_ada.status_code == 401
        assert ada.json()["error"] == tidak_ada.json()["error"]


class TestPemisahanPrincipal:
    async def test_token_device_tidak_bisa_membaca_armada(self, stack: Stack) -> None:
        """Kredensial device yang bocor dari satu kapal tidak boleh bisa dipakai
        membaca seluruh armada."""
        token = await _device_token(stack.client)
        response = await stack.client.get(
            "/api/v1/ships", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "auth.wrong_token_type"

    async def test_token_user_tidak_bisa_menyetor_telemetry(self, stack: Stack) -> None:
        token = await _login(stack.client, "admin")
        response = await stack.client.get(
            "/api/v1/ingest/sync-state", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 401


class TestOtorisasi:
    async def test_peran_ditegakkan_untuk_perubahan(self, stack: Stack) -> None:
        token = await _login(stack.client, "pengamat")
        headers = {"Authorization": f"Bearer {token}"}

        for method, path, body in (
            ("post", "/api/v1/ships", {"name": "X", "slug": "X"}),
            ("post", "/api/v1/devices", {"ship_id": str(uuid4()), "name": "X"}),
            ("get", "/api/v1/audit", None),
        ):
            response = await getattr(stack.client, method)(
                path, headers=headers, **({"json": body} if body else {})
            )
            # 403, bukan 401. Keduanya menolak, tetapi klien memperlakukannya
            # berbeda: dashboard menghapus sesi pada 401 apa pun, sehingga
            # operator yang menekan tombol yang bukan haknya akan terlempar
            # keluar alih-alih diberi tahu bahwa ia tidak berhak.
            assert response.status_code == 403, f"{method} {path}"
            assert response.json()["error"]["code"] == "auth.forbidden"

        # Sebaliknya, token yang benar-benar tidak sah harus tetap 401 —
        # pembedaan ini yang membuat "sesi habis" bisa dibedakan dari "tidak
        # berhak".
        bad = await stack.client.get(
            "/api/v1/ships", headers={"Authorization": "Bearer token-palsu"}
        )
        assert bad.status_code == 401


class TestKerahasiaanData:
    async def test_sandi_tidak_disimpan_sebagai_teks_biasa(self, stack: Stack) -> None:
        async with stack.db.session() as session:
            rows = await session.execute(text("SELECT password_hash FROM users"))
            for (stored,) in rows:
                assert PASSWORD not in stored
                assert stored.startswith("$argon2")

    async def test_hash_sandi_bergaram(self, stack: Stack) -> None:
        """Dua sandi identik harus menghasilkan hash berbeda; kalau tidak, satu
        tabel pelangi cukup untuk seluruh basis pengguna."""
        assert hash_secret(PASSWORD) != hash_secret(PASSWORD)
        assert verify_secret(hash_secret(PASSWORD), PASSWORD)

    async def test_kredensial_device_juga_di_hash(self, stack: Stack) -> None:
        async with stack.db.session() as session:
            rows = await session.execute(text("SELECT secret_hash FROM device_credentials"))
            for (stored,) in rows:
                assert "rahasia-device" not in stored

    async def test_response_tidak_pernah_memuat_hash(self, stack: Stack) -> None:
        token = await _login(stack.client, "admin")
        for path in ("/api/v1/auth/me", "/api/v1/ships", "/api/v1/devices"):
            body = (await stack.client.get(path, headers={"Authorization": f"Bearer {token}"})).text
            assert "argon2" not in body
            assert "password" not in body.lower()

    def test_redaksi_menangkap_pola_rahasia(self) -> None:
        out = redact_config(
            {
                "influx_token": "x",
                "jwt_secret": "y",
                "db_password": "z",
                "postgres_dsn": "postgresql://u:p@h/db",
                "api_key": "k",
                "nama_kapal": "KM Uji",
            }
        )
        for key in ("influx_token", "jwt_secret", "db_password", "postgres_dsn", "api_key"):
            assert out[key] not in ("x", "y", "z", "k")
            assert "postgresql://" not in str(out[key])
        assert out["nama_kapal"] == "KM Uji"


class TestKebocoranLewatError:
    async def test_error_internal_tidak_membocorkan_detail(self, stack: Stack) -> None:
        """Pesan exception internal sering memuat DSN, path, atau potongan query."""
        token = await _login(stack.client, "admin")
        response = await stack.client.get(
            "/api/v1/ships/bukan-uuid", headers={"Authorization": f"Bearer {token}"}
        )
        body = response.text
        assert "postgresql" not in body
        assert "Traceback" not in body
        assert "/Users/" not in body

    async def test_openapi_tertutup_di_produksi(self) -> None:
        settings = ApiSettings(
            environment="production",
            log_format="json",
            jwt_secret="x" * 32,
            influx_token="t",
            postgres_dsn="postgresql+asyncpg://u:p@db:5432/f",
        )
        app = create_app(settings)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://sec.test"
        ) as client:
            assert (await client.get("/openapi.json")).status_code == 404
            assert (await client.get("/docs")).status_code == 404


class TestInjeksi:
    async def test_parameter_pencarian_tidak_bisa_menyuntik_sql(self, stack: Stack) -> None:
        """SQLAlchemy memakai parameter terikat, tetapi test ini menjaga agar
        tidak ada yang menggantinya dengan penyusunan string."""
        token = await _login(stack.client, "admin")

        for payload in ("'; DROP TABLE ships; --", "' OR '1'='1", "%"):
            response = await stack.client.get(
                "/api/v1/ships",
                params={"q": payload},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert response.status_code == 200

        # Tabelnya harus masih ada beserta isinya.
        async with stack.db.session() as session:
            count = await session.scalar(text("SELECT count(*) FROM ships"))
        assert count == 1

    async def test_envelope_batch_salah_bentuk_ditolak_dengan_rapi(self, stack: Stack) -> None:
        token = await _device_token(stack.client)
        for bad in ("{}", "bukan json", '{"batch_id": "bukan-uuid"}'):
            response = await stack.client.post(
                "/api/v1/ingest/batches",
                content=b"x",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/octet-stream",
                    "X-Batch-Envelope": bad,
                },
            )
            assert response.status_code in (400, 422)
            assert "Traceback" not in response.text


class TestKonfigurasiAman:
    def test_cors_tertutup_secara_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Daftar origin yang longgar adalah cara paling mudah membocorkan API
        internal ke halaman web mana pun.

        `_env_file=None` dan pembersihan env wajib di sini: tanpa itu test ini
        membaca `.env` pengembang — yang justru langkah wajib di panduan
        menjalankan — sehingga gagal di mesin siapa pun yang mengikutinya,
        tetapi lolos di CI yang tidak punya `.env`.
        """
        for key in list(os.environ):
            if key.startswith("FLEETVIEW_"):
                monkeypatch.delenv(key, raising=False)
        assert ApiSettings(_env_file=None).cors_origins == []

    def test_produksi_menolak_rahasia_lemah(self) -> None:
        from fleetview_common import ConfigError

        base: dict[str, object] = {
            "environment": "production",
            "log_format": "json",
            "jwt_secret": "x" * 32,
            "influx_token": "t",
            "postgres_dsn": "postgresql+asyncpg://u:p@db:5432/f",
        }
        for override in ({"jwt_secret": ""}, {"jwt_secret": "pendek"}, {"influx_token": ""}):
            settings = ApiSettings(**{**base, **override})  # type: ignore[arg-type]
            with pytest.raises(ConfigError):
                settings.verify_production_ready()

    def test_edge_produksi_menolak_console_terbuka(self) -> None:
        from fleetview_common import ConfigError
        from fleetview_edge.settings import EdgeSettings

        settings = EdgeSettings(
            environment="production",
            ship={"ship_id": str(uuid4()), "ship_name": "X", "device_id": str(uuid4())},
            storage={"influx_token": "t"},
            sync={"central_url": "https://x"},
            collector={"adapter": "lp_a104", "sensors_path": "/etc/x.yaml"},
            console={"host": "0.0.0.0"},
        )
        with pytest.raises(ConfigError, match=r"0\.0\.0\.0"):
            settings.verify_production_ready()

    def test_edge_produksi_menolak_data_simulasi(self) -> None:
        """Kapal yang mengirim data simulasi akan tampak sehat sempurna di
        dashboard, dan tidak akan ada yang menyadarinya."""
        from fleetview_common import ConfigError
        from fleetview_edge.settings import EdgeSettings

        settings = EdgeSettings(
            environment="production",
            ship={"ship_id": str(uuid4()), "ship_name": "X", "device_id": str(uuid4())},
            storage={"influx_token": "t"},
            sync={"central_url": "https://x"},
            collector={"adapter": "simulator", "sensors_path": "/etc/x.yaml"},
        )
        with pytest.raises(ConfigError, match="simulator"):
            settings.verify_production_ready()


class TestAuditTidakBisaDiubah:
    async def test_login_dan_perubahan_tercatat(self, stack: Stack) -> None:
        token = await _login(stack.client, "admin")
        headers = {"Authorization": f"Bearer {token}"}
        await stack.client.post(
            "/api/v1/ships", json={"name": "KM Audit", "slug": "SHIP-AUD"}, headers=headers
        )

        entries = (await stack.client.get("/api/v1/audit", headers=headers)).json()["data"]
        actions = {e["action"] for e in entries}
        assert {"auth.login", "fleet.ship_created"} <= actions

    async def test_tidak_ada_endpoint_untuk_menghapus_audit(self, stack: Stack) -> None:
        token = await _login(stack.client, "admin")
        headers = {"Authorization": f"Bearer {token}"}
        for method in ("delete", "put", "patch"):
            response = await getattr(stack.client, method)("/api/v1/audit", headers=headers)
            assert response.status_code in (404, 405)
