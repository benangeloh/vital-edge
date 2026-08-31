"""Integration test tingkat API: autentikasi, RBAC, dan jalur ingest lewat HTTP.

Memakai aplikasi FastAPI sungguhan terhadap Postgres dan InfluxDB sungguhan.
Yang diuji di sini adalah hal-hal yang hanya muncul di batas HTTP: pemisahan
token user dan device, penegakan peran, bentuk envelope, dan pemetaan status.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from uuid import UUID

import pytest

from fleetview_central.modules.fleet.models import Device, Ship
from fleetview_central.modules.identity.service import IdentityService
from fleetview_central.modules.ingest.models import ShipSyncState
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
        assert r.status_code == 401
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
