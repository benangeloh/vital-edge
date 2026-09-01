"""Integration test tingkat API: autentikasi, RBAC, dan jalur ingest lewat HTTP.

Memakai aplikasi FastAPI sungguhan terhadap Postgres dan InfluxDB sungguhan.
Yang diuji di sini adalah hal-hal yang hanya muncul di batas HTTP: pemisahan
token user dan device, penegakan peran, bentuk envelope, dan pemetaan status.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import select

from fleetview_central.modules.fleet.models import Device, Ship
from fleetview_central.modules.identity.service import IdentityService
from fleetview_central.modules.ingest.models import ShipSyncState
from fleetview_central.modules.ops.models import AuditLog
from fleetview_common import from_micros, now_utc, uuid7
from fleetview_contracts import SCHEMA_VERSION, BatchEnvelope, Quality, Reading

pytestmark = pytest.mark.integration


@pytest.fixture
async def seeded(session, tokens, ship_id, device_id):
    """Satu kapal, satu perangkat, satu admin, satu kredensial device."""
    # Ship di-flush lebih dulu: Device dan ShipSyncState punya foreign key
    # padanya, dan urutan insert dalam satu flush tidak dijamin sesuai urutan add.
    session.add(Ship(id=ship_id, name="KM Sinar Jaya", slug="SHIP-023"))
    await session.flush()
    session.add(Device(id=device_id, ship_id=ship_id, name="Edge Pi"))
    session.add(ShipSyncState(ship_id=ship_id))
    await session.flush()

    identity = IdentityService(session, tokens)
    await identity.create_user(
        username="operator", password="rahasia-yang-panjang", roles=["admin"]
    )
    await identity.create_user(username="pengamat", password="rahasia-yang-panjang")
    await identity.create_device_credential(
        device_id=device_id,
        ship_id=ship_id,
        client_id="ship-023",
        secret="rahasia-device-yang-panjang",
    )
    await session.commit()
    return {"ship_id": ship_id, "device_id": device_id}


async def _user_token(client, username="operator") -> str:
    r = await client.post(
        "/api/v1/auth/login", json={"username": username, "password": "rahasia-yang-panjang"}
    )
    assert r.status_code == 200, r.text
    return str(r.json()["data"]["access_token"])


async def _device_token(client) -> str:
    r = await client.post(
        "/api/v1/auth/device/token",
        json={"client_id": "ship-023", "secret": "rahasia-device-yang-panjang"},
    )
    assert r.status_code == 200, r.text
    return str(r.json()["data"]["access_token"])


def _batch(
    ship_id: UUID, device_id: UUID, n: int = 5, start: int = 1
) -> tuple[BatchEnvelope, bytes]:
    base = int(now_utc().timestamp() * 1_000_000) - 3600 * 1_000_000
    readings = [
        Reading(
            seq=i,
            ts=base + i * 1_000_000,
            sensor_id="me_port_rpm",
            measurement="rpm",
            fields={"value": float(700 + i)},
            unit="rpm",
            quality=Quality.GOOD,
        )
        for i in range(start, start + n)
    ]
    payload = gzip.compress(
        json.dumps([r.model_dump(mode="json") for r in readings], separators=(",", ":")).encode(),
        mtime=0,
    )
    env = BatchEnvelope(
        schema_version=SCHEMA_VERSION,
        batch_id=uuid7(),
        ship_id=ship_id,
        device_id=device_id,
        sequence_start=readings[0].seq,
        sequence_end=readings[-1].seq,
        first_timestamp=from_micros(min(r.ts for r in readings)),
        last_timestamp=from_micros(max(r.ts for r in readings)),
        record_count=len(readings),
        payload_checksum=f"sha256:{hashlib.sha256(payload).hexdigest()}",
        payload_encoding="gzip+json",
        payload_size=len(payload),
        agent_version="0.1.0",
        config_version="cfg-1",
        created_at=now_utc(),
    )
    return env, payload


class TestAutentikasi:
    async def test_login_berhasil(self, client, seeded) -> None:
        r = await client.post(
            "/api/v1/auth/login",
            json={"username": "operator", "password": "rahasia-yang-panjang"},
        )
        assert r.status_code == 200
        assert r.json()["ok"] is True

    async def test_sandi_salah_ditolak(self, client, seeded) -> None:
        r = await client.post(
            "/api/v1/auth/login", json={"username": "operator", "password": "salah"}
        )
        assert r.status_code == 401
        assert r.json()["error"]["retryable"] is False

    async def test_user_tidak_ada_pesan_sama(self, client, seeded) -> None:
        """Pesan yang sama untuk user tidak ada dan sandi salah — kalau berbeda,
        penyerang bisa menyusun daftar username yang terdaftar."""
        r = await client.post(
            "/api/v1/auth/login", json={"username": "hantu", "password": "apa-saja"}
        )
        assert r.status_code == 401
        assert "username atau kata sandi" in r.json()["error"]["message"]

    async def test_tanpa_token_ditolak(self, client, seeded) -> None:
        assert (await client.get("/api/v1/ships")).status_code == 401

    async def test_token_device_tidak_bisa_pakai_endpoint_user(self, client, seeded) -> None:
        """Kredensial device yang bocor dari satu kapal tidak boleh bisa dipakai
        membaca seluruh armada."""
        token = await _device_token(client)
        r = await client.get("/api/v1/ships", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "auth.wrong_token_type"

    async def test_token_user_tidak_bisa_pakai_endpoint_ingest(self, client, seeded) -> None:
        token = await _user_token(client)
        r = await client.get(
            "/api/v1/ingest/sync-state", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 401

    async def test_token_device_terikat_pada_kapal(self, client, seeded) -> None:
        r = await client.post(
            "/api/v1/auth/device/token",
            json={"client_id": "ship-023", "secret": "rahasia-device-yang-panjang"},
        )
        assert r.json()["data"]["ship_id"] == str(seeded["ship_id"])


class TestRbac:
    async def test_tanpa_peran_tidak_bisa_membuat_kapal(self, client, seeded) -> None:
        token = await _user_token(client, "pengamat")
        r = await client.post(
            "/api/v1/ships",
            json={"name": "KM Baru", "slug": "SHIP-999"},
            headers={"Authorization": f"Bearer {token}"},
        )
        # 403, bukan 401. Dashboard menghapus sesi pada 401 apa pun; kalau
        # kekurangan peran juga 401, operator yang menekan tombol yang bukan
        # haknya akan terlempar keluar alih-alih diberi tahu.
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "auth.forbidden"

    async def test_admin_bisa_membuat_kapal(self, client, seeded) -> None:
        token = await _user_token(client)
        r = await client.post(
            "/api/v1/ships",
            json={"name": "KM Baru", "slug": "SHIP-999"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 201

    async def test_membaca_armada_boleh_tanpa_peran_khusus(self, client, seeded) -> None:
        token = await _user_token(client, "pengamat")
        r = await client.get("/api/v1/ships", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert len(r.json()["data"]) == 1


class TestIngestHttp:
    async def test_batch_diterima_dan_di_ack(self, client, seeded) -> None:
        token = await _device_token(client)
        env, payload = _batch(seeded["ship_id"], seeded["device_id"])
        r = await client.post(
            "/api/v1/ingest/batches",
            content=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/octet-stream",
                "X-Batch-Envelope": env.model_dump_json(),
            },
        )
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["batch_id"] == str(env.batch_id)
        assert data["last_contiguous_sequence"] == 5

    async def test_kiriman_ulang_idempoten_lewat_http(self, client, seeded) -> None:
        token = await _device_token(client)
        env, payload = _batch(seeded["ship_id"], seeded["device_id"])
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/octet-stream",
            "X-Batch-Envelope": env.model_dump_json(),
        }
        first = await client.post("/api/v1/ingest/batches", content=payload, headers=headers)
        second = await client.post("/api/v1/ingest/batches", content=payload, headers=headers)
        assert first.json()["data"] == second.json()["data"]

    async def test_checksum_rusak_ditolak_tanpa_retry(self, client, seeded) -> None:
        """4xx berarti jangan diulang. Kalau salah dipetakan jadi 5xx, edge akan
        mengulang selamanya dan antreannya tersumbat."""
        token = await _device_token(client)
        env, payload = _batch(seeded["ship_id"], seeded["device_id"])
        r = await client.post(
            "/api/v1/ingest/batches",
            content=payload + b"rusak",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/octet-stream",
                "X-Batch-Envelope": env.model_dump_json(),
            },
        )
        assert r.status_code == 400
        assert r.json()["error"]["retryable"] is False
        assert r.json()["error"]["code"] == "ingest.checksum_mismatch"

    async def test_sync_state_melaporkan_watermark(self, client, seeded) -> None:
        token = await _device_token(client)
        env, payload = _batch(seeded["ship_id"], seeded["device_id"])
        await client.post(
            "/api/v1/ingest/batches",
            content=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/octet-stream",
                "X-Batch-Envelope": env.model_dump_json(),
            },
        )
        r = await client.get(
            "/api/v1/ingest/sync-state", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.json()["data"]["last_contiguous_sequence"] == 5

    async def test_heartbeat_memperbarui_status(self, client, seeded) -> None:
        token = await _device_token(client)
        r = await client.post(
            "/api/v1/ingest/heartbeat",
            json={
                "ship_id": str(seeded["ship_id"]),
                "device_id": str(seeded["device_id"]),
                "sent_at": now_utc().isoformat(),
                "agent_version": "0.1.0",
                "config_version": "cfg-1",
                "pending_batches": 3,
                "pending_records": 150,
                "oldest_pending_age_seconds": 900,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        assert r.json()["data"]["server_time"]

        user = await _user_token(client)
        ships = await client.get("/api/v1/ships", headers={"Authorization": f"Bearer {user}"})
        assert ships.json()["data"][0]["pending_estimate"] == 150


class TestArmadaDanKonfigurasi:
    async def test_tampilan_armada_menyertakan_status_sync(self, client, seeded) -> None:
        token = await _user_token(client)
        r = await client.get("/api/v1/ships", headers={"Authorization": f"Bearer {token}"})
        ship = r.json()["data"][0]
        assert ship["name"] == "KM Sinar Jaya"
        assert ship["connection_state"] == "offline"  # belum ada batch masuk
        assert ship["has_gap"] is False

    async def test_sensor_muncul_setelah_ingest(self, client, seeded) -> None:
        token = await _device_token(client)
        env, payload = _batch(seeded["ship_id"], seeded["device_id"])
        await client.post(
            "/api/v1/ingest/batches",
            content=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/octet-stream",
                "X-Batch-Envelope": env.model_dump_json(),
            },
        )
        user = await _user_token(client)
        r = await client.get(
            f"/api/v1/ships/{seeded['ship_id']}/sensors",
            headers={"Authorization": f"Bearer {user}"},
        )
        sensors = r.json()["data"]
        assert len(sensors) == 1
        assert sensors[0]["sensor_id"] == "me_port_rpm"
        assert sensors[0]["status"] == "unregistered"

    async def test_konfigurasi_berversi(self, client, seeded) -> None:
        token = await _user_token(client)
        h = {"Authorization": f"Bearer {token}"}
        await client.put(
            f"/api/v1/config/{seeded['ship_id']}",
            json={"version": "cfg-1", "payload": {"poll": 1.0}},
            headers=h,
        )
        await client.put(
            f"/api/v1/config/{seeded['ship_id']}",
            json={"version": "cfg-2", "payload": {"poll": 0.5}},
            headers=h,
        )
        r = await client.get(f"/api/v1/config/{seeded['ship_id']}", headers=h)
        versions = r.json()["data"]
        assert len(versions) == 2
        assert [v["is_active"] for v in versions] == [True, False]

    async def test_versi_ganda_ditolak(self, client, seeded) -> None:
        token = await _user_token(client)
        h = {"Authorization": f"Bearer {token}"}
        body = {"version": "cfg-1", "payload": {}}
        await client.put(f"/api/v1/config/{seeded['ship_id']}", json=body, headers=h)
        r = await client.put(f"/api/v1/config/{seeded['ship_id']}", json=body, headers=h)
        assert r.status_code == 400


class TestObservabilitas:
    async def test_health_memeriksa_kedua_database(self, client, seeded) -> None:
        r = await client.get("/api/v1/health")
        checks = r.json()["data"]["checks"]
        assert checks["postgres"] == "ok"
        assert checks["influxdb"] == "ok"

    async def test_audit_mencatat_login_dan_perubahan(self, client, seeded) -> None:
        token = await _user_token(client)
        h = {"Authorization": f"Bearer {token}"}
        await client.post("/api/v1/ships", json={"name": "KM B", "slug": "SHIP-002"}, headers=h)

        r = await client.get("/api/v1/audit", headers=h)
        actions = {a["action"] for a in r.json()["data"]}
        assert "auth.login" in actions
        assert "fleet.ship_created" in actions

    async def test_openapi_terbit(self, client, seeded) -> None:
        r = await client.get("/openapi.json")
        assert r.status_code == 200
        paths = r.json()["paths"]
        for expected in (
            "/api/v1/auth/login",
            "/api/v1/ships",
            "/api/v1/devices",
            "/api/v1/ingest/batches",
            "/api/v1/telemetry/series",
            "/api/v1/health",
            "/api/v1/alerts",
        ):
            assert expected in paths, f"{expected} tidak ada di OpenAPI"


class TestTelemetryJson:
    """Chart butuh JSON, bukan CSV Flux mentah."""

    async def test_series_terurai_jadi_json(self, client, seeded) -> None:
        token = await _device_token(client)
        env, payload = _batch(seeded["ship_id"], seeded["device_id"], n=5)
        await client.post(
            "/api/v1/ingest/batches",
            content=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/octet-stream",
                "X-Batch-Envelope": env.model_dump_json(),
            },
        )
        user = await _user_token(client)
        r = await client.get(
            f"/api/v1/telemetry/series?ship_id={seeded['ship_id']}&range_seconds=7200",
            headers={"Authorization": f"Bearer {user}"},
        )
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["bucket_used"] == "raw"
        series = data["series"]
        assert len(series) >= 1
        first = series[0]
        assert first["sensor_id"] == "me_port_rpm"
        assert first["measurement"] == "rpm"
        assert len(first["points"]) >= 1
        ts, value = first["points"][0]
        assert isinstance(ts, str) and isinstance(value, float)

    async def test_kapal_tanpa_data_mengembalikan_daftar_kosong(self, client, seeded) -> None:
        """Bukan error. Kapal baru memang belum punya telemetry."""
        user = await _user_token(client)
        r = await client.get(
            f"/api/v1/telemetry/series?ship_id={seeded['ship_id']}",
            headers={"Authorization": f"Bearer {user}"},
        )
        assert r.status_code == 200
        assert r.json()["data"]["series"] == []


class TestAlamatKlienAuditLog:
    """Audit log harus mencatat alamat kapal yang sebenarnya.

    Di produksi central berjalan di belakang nginx. Tanpa membaca
    `X-Forwarded-For`, setiap baris audit akan berbunyi 127.0.0.1 dan tidak bisa
    dipakai menyelidiki apa pun — yang justru menghapus alasan audit log ada.
    """

    async def _ip_tercatat(self, session, client, headers: dict[str, str]) -> str | None:
        from sqlalchemy import desc, select

        await client.post(
            "/api/v1/auth/login",
            json={"username": "operator", "password": "rahasia-yang-panjang"},
            headers=headers,
        )
        row = (
            await session.execute(select(AuditLog).order_by(desc(AuditLog.created_at)).limit(1))
        ).scalar_one()
        ip: str | None = row.ip_address
        return ip

    async def test_header_diabaikan_secara_default(self, seeded, session, client) -> None:
        """Default tidak mempercayai header, dan itu disengaja: header ini
        dikirim klien, jadi mempercayainya tanpa proxy di depan berarti siapa pun
        bisa memalsukan alamatnya sendiri di audit log."""
        client._transport.app.state.settings.trust_proxy_headers = False
        ip = await self._ip_tercatat(session, client, {"X-Forwarded-For": "203.0.113.9"})
        assert ip != "203.0.113.9"

    async def test_header_dipakai_saat_proxy_dipercaya(self, seeded, session, client) -> None:
        client._transport.app.state.settings.trust_proxy_headers = True
        ip = await self._ip_tercatat(session, client, {"X-Forwarded-For": "203.0.113.9"})
        assert ip == "203.0.113.9"

    async def test_entri_palsu_di_depan_tidak_menang(self, seeded, session, client) -> None:
        """nginx menambahkan alamat peer yang benar-benar ia lihat ke UJUNG
        header. Entri sebelumnya berasal dari klien dan bisa dipalsukan
        seluruhnya, jadi yang diambil harus entri terakhir."""
        client._transport.app.state.settings.trust_proxy_headers = True
        ip = await self._ip_tercatat(session, client, {"X-Forwarded-For": "1.2.3.4, 198.51.100.7"})
        assert ip == "198.51.100.7"


class TestPencabutanKredensial:
    """Pencabutan harus benar-benar menutup akses.

    Prosedur decommissioning bergantung sepenuhnya pada ini: kapal yang dijual
    tidak boleh bisa terus mengirim data, dan perangkat lama yang diganti tidak
    boleh menyimpan akses yang masih hidup.
    """

    async def test_kredensial_dicabut_ditolak(self, seeded, session, tokens, client) -> None:
        from fleetview_central.modules.identity.service import IdentityService

        body = {"client_id": "ship-023", "secret": "rahasia-device-yang-panjang"}
        assert (await client.post("/api/v1/auth/device/token", json=body)).status_code == 200

        identity = IdentityService(session, tokens)
        assert await identity.revoke_device_credential("ship-023") is not None
        await session.commit()

        r = await client.post("/api/v1/auth/device/token", json=body)
        assert r.status_code == 401, "kredensial dicabut masih diterima"

    async def test_baris_kredensial_tidak_dihapus(self, seeded, session, tokens) -> None:
        """Ditandai, bukan dihapus: audit log dan penelusuran insiden merujuk
        padanya, dan pertanyaan 'kredensial mana yang dipakai, kapan dicabut'
        hanya bisa dijawab kalau barisnya masih ada."""
        from fleetview_central.modules.identity.models import DeviceCredential
        from fleetview_central.modules.identity.service import IdentityService

        identity = IdentityService(session, tokens)
        await identity.revoke_device_credential("ship-023")
        await session.commit()

        cred = (
            await session.execute(
                select(DeviceCredential).where(DeviceCredential.client_id == "ship-023")
            )
        ).scalar_one()
        assert cred.revoked_at is not None
        assert cred.is_active is False

    async def test_mencabut_yang_tidak_ada_bukan_error(self, seeded, session, tokens) -> None:
        from fleetview_central.modules.identity.service import IdentityService

        assert await IdentityService(session, tokens).revoke_device_credential("tidak-ada") is None


class TestSequenceTerpakaiUlang:
    """Nomor sequence yang mundur harus menghasilkan pesan yang bisa
    ditindaklanjuti, bukan 500 dengan stack trace.

    Ini bukan kasus teoretis: perangkat yang diganti tanpa menaikkan counter dan
    outbox yang dipulihkan dari cadangan lama keduanya menghasilkannya, dan
    keduanya adalah prosedur yang tertulis di docs/operations/.
    """

    async def test_rentang_sama_batch_id_beda_ditolak_dengan_jelas(self, client, seeded) -> None:
        token = await _device_token(client)
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/octet-stream",
        }

        env, payload = _batch(seeded["ship_id"], seeded["device_id"])
        r1 = await client.post(
            "/api/v1/ingest/batches",
            content=payload,
            headers={**headers, "X-Batch-Envelope": env.model_dump_json()},
        )
        assert r1.status_code == 200, r1.text

        # batch_id baru, rentang sequence sama persis — inilah yang terjadi saat
        # perangkat diganti tanpa menaikkan counter sequence.
        env2, payload2 = _batch(seeded["ship_id"], seeded["device_id"])
        assert env2.batch_id != env.batch_id
        assert (env2.sequence_start, env2.sequence_end) == (
            env.sequence_start,
            env.sequence_end,
        )

        r2 = await client.post(
            "/api/v1/ingest/batches",
            content=payload2,
            headers={**headers, "X-Batch-Envelope": env2.model_dump_json()},
        )

        assert r2.status_code != 500, f"500 tanpa penanganan: {r2.text[:300]}"
        body = r2.json()
        assert body["error"]["code"] == "ingest.sequence_reused"
        assert body["error"]["retryable"] is False, (
            "mencoba ulang tidak akan pernah berhasil; retryable membuat kapal "
            "membombardir central sambil menyembunyikan sebabnya"
        )
        assert "tindakan" in body["error"]["details"]

    async def test_kiriman_ulang_batch_id_sama_tetap_memutar_ack(self, client, seeded) -> None:
        """Jangan sampai perbaikan di atas merusak idempotensi biasa."""
        token = await _device_token(client)
        env, payload = _batch(seeded["ship_id"], seeded["device_id"], start=500)
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/octet-stream",
            "X-Batch-Envelope": env.model_dump_json(),
        }
        first = await client.post("/api/v1/ingest/batches", content=payload, headers=headers)
        second = await client.post("/api/v1/ingest/batches", content=payload, headers=headers)
        assert first.status_code == 200 and second.status_code == 200
        assert first.json()["data"]["batch_id"] == second.json()["data"]["batch_id"]


class TestOnboardingKapal:
    """Onboarding lewat dashboard: satu panggilan menghasilkan kapal, perangkat,
    dan kredensial yang benar-benar bisa dipakai Edge Agent."""

    async def _onboard(self, client, token, **over) -> Any:
        body = {"name": "KM Baru", "slug": "SHIP-900", **over}
        return await client.post(
            "/api/v1/ships/onboard",
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )

    async def test_kapal_perangkat_kredensial_sekali_jalan(self, seeded, client) -> None:
        token = await _user_token(client)
        r = await self._onboard(client, token)
        assert r.status_code == 201, r.text
        d = r.json()["data"]
        assert d["ship_id"] and d["device_id"] and d["client_id"] == "ship-900"
        assert d["client_secret"]

    async def test_kredensial_hasil_onboarding_langsung_bisa_dipakai(self, seeded, client) -> None:
        """Inilah yang menentukan apakah fitur ini berguna: Raspberry Pi yang
        diisi nilai dari layar ini harus benar-benar bisa menyetor data."""
        token = await _user_token(client)
        d = (await self._onboard(client, token, slug="SHIP-901")).json()["data"]

        r = await client.post(
            "/api/v1/auth/device/token",
            json={"client_id": d["client_id"], "secret": d["client_secret"]},
        )
        assert r.status_code == 200, r.text
        assert r.json()["data"]["access_token"]

    async def test_rahasia_tidak_pernah_bisa_dibaca_ulang(self, seeded, client) -> None:
        """Di basis data hanya ada hash Argon2id. Daftar kredensial tidak boleh
        mengembalikan apa pun yang menyerupai rahasia."""
        token = await _user_token(client)
        d = (await self._onboard(client, token, slug="SHIP-902")).json()["data"]

        r = await client.get(
            f"/api/v1/devices/{d['device_id']}/credentials",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        assert d["client_secret"] not in r.text
        assert "secret" not in r.text.lower()

    async def test_rahasia_tidak_masuk_audit_log(self, seeded, client) -> None:
        """Audit log dibaca jauh lebih luas daripada yang boleh melihat
        kredensial perangkat."""
        token = await _user_token(client)
        d = (await self._onboard(client, token, slug="SHIP-903")).json()["data"]

        r = await client.get("/api/v1/audit", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert d["client_secret"] not in r.text

    async def test_slug_bentrok_ditolak_sebelum_membuat_apa_pun(self, seeded, client) -> None:
        """Kalau ditolak di tengah, akan tertinggal kapal tanpa perangkat yang
        harus dibereskan lewat SSH ke server."""
        token = await _user_token(client)
        assert (await self._onboard(client, token, slug="SHIP-904")).status_code == 201
        r = await self._onboard(client, token, slug="SHIP-904", name="KM Lain")
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "fleet.slug_taken"

        ships = (
            await client.get("/api/v1/ships", headers={"Authorization": f"Bearer {token}"})
        ).json()["data"]
        assert len([s for s in ships if s["slug"] == "SHIP-904"]) == 1

    async def test_hanya_admin_boleh_onboarding(self, seeded, client) -> None:
        token = await _user_token(client, username="pengamat")
        r = await self._onboard(client, token, slug="SHIP-905")
        assert r.status_code == 403, "403, bukan 401 — 401 membuat dashboard logout"

    async def test_pencabutan_menutup_akses_perangkat(self, seeded, client) -> None:
        token = await _user_token(client)
        d = (await self._onboard(client, token, slug="SHIP-906")).json()["data"]
        creds = {"client_id": d["client_id"], "secret": d["client_secret"]}
        assert (await client.post("/api/v1/auth/device/token", json=creds)).status_code == 200

        r = await client.post(
            f"/api/v1/credentials/{d['client_id']}/revoke",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        assert (await client.post("/api/v1/auth/device/token", json=creds)).status_code == 401

    async def test_terbitkan_ulang_untuk_perangkat_pengganti(self, seeded, client) -> None:
        """Saat Raspberry Pi diganti. Kredensial lama TIDAK otomatis dicabut:
        perangkat lama mungkin masih menyetor sisa backlog-nya."""
        token = await _user_token(client)
        d = (await self._onboard(client, token, slug="SHIP-907")).json()["data"]
        lama = {"client_id": d["client_id"], "secret": d["client_secret"]}

        r = await client.post(
            f"/api/v1/devices/{d['device_id']}/credentials",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 201
        baru = r.json()["data"]
        assert baru["client_id"] != lama["client_id"]

        for creds in (lama, {"client_id": baru["client_id"], "secret": baru["client_secret"]}):
            assert (await client.post("/api/v1/auth/device/token", json=creds)).status_code == 200
