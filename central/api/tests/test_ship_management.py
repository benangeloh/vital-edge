"""Mengelola kapal dari dashboard: ubah, lihat ulang kredensial, nonaktifkan.

Tiga hal ini dikerjakan admin dari daftar kapal. Yang diuji di sini adalah
janji-janji yang membuatnya aman dipakai: rahasia bisa dibuka lagi tanpa pernah
tersimpan sebagai teks, kapal yang dinonaktifkan benar-benar kehilangan akses,
dan datanya tidak ikut hilang.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import select

from fleetview_central.modules.fleet.models import Device, Ship
from fleetview_central.modules.identity.models import DeviceCredential
from fleetview_central.modules.identity.service import IdentityService
from fleetview_central.modules.ingest.models import ShipSyncState

pytestmark = pytest.mark.integration

PASSWORD = "rahasia-yang-panjang"


@pytest.fixture
async def users(session, tokens):
    identity = IdentityService(session, tokens)
    await identity.create_user(username="operator", password=PASSWORD, roles=["admin"])
    await identity.create_user(username="pengamat", password=PASSWORD)
    await session.commit()


async def _login(client, username: str = "operator") -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['data']['access_token']}"}


async def _onboard(client, headers, slug: str = "SHIP-700", **over: Any) -> dict[str, Any]:
    r = await client.post(
        "/api/v1/ships/onboard",
        json={"name": "KM Uji", "slug": slug, **over},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    data: dict[str, Any] = r.json()["data"]
    return data


async def _device_login(client, data: dict[str, Any]) -> int:
    r = await client.post(
        "/api/v1/auth/device/token",
        json={"client_id": data["client_id"], "secret": data["client_secret"]},
    )
    return int(r.status_code)


class TestLihatKredensialLagi:
    """Skrip pasang bisa diunduh ulang dengan kredensial yang SAMA."""

    async def test_rahasia_yang_sama_tampil_lagi(self, users, client) -> None:
        h = await _login(client)
        awal = await _onboard(client, h)

        r = await client.get(f"/api/v1/ships/{awal['ship_id']}/credential", headers=h)
        assert r.status_code == 200, r.text
        lagi = r.json()["data"]
        assert lagi["client_id"] == awal["client_id"]
        assert lagi["client_secret"] == awal["client_secret"]

    async def test_isi_cukup_untuk_menyusun_skrip_pasang(self, users, client) -> None:
        h = await _login(client)
        awal = await _onboard(client, h)
        lagi = (await client.get(f"/api/v1/ships/{awal['ship_id']}/credential", headers=h)).json()[
            "data"
        ]
        for field in ("ship_id", "slug", "name", "device_id", "client_id", "client_secret"):
            assert lagi[field] == awal[field], field

    async def test_onboarding_memberi_tahu_bahwa_kredensial_bisa_dibuka_lagi(
        self, users, client
    ) -> None:
        awal = await _onboard(client, await _login(client))
        assert awal["recoverable"] is True

    async def test_dibuka_berkali_kali_tetap_sama(self, users, client) -> None:
        h = await _login(client)
        awal = await _onboard(client, h)
        url = f"/api/v1/ships/{awal['ship_id']}/credential"
        a = (await client.get(url, headers=h)).json()["data"]
        b = (await client.get(url, headers=h)).json()["data"]
        assert a["client_secret"] == b["client_secret"] == awal["client_secret"]

    async def test_hasilnya_langsung_bisa_dipakai_kapal(self, users, client) -> None:
        h = await _login(client)
        awal = await _onboard(client, h)
        lagi = (await client.get(f"/api/v1/ships/{awal['ship_id']}/credential", headers=h)).json()[
            "data"
        ]
        assert await _device_login(client, lagi) == 200

    async def test_di_basis_data_tidak_pernah_berupa_teks(self, users, client, session) -> None:
        h = await _login(client)
        awal = await _onboard(client, h)

        cred = (
            await session.execute(
                select(DeviceCredential).where(DeviceCredential.client_id == awal["client_id"])
            )
        ).scalar_one()
        assert cred.secret_encrypted is not None
        assert awal["client_secret"] not in cred.secret_encrypted
        assert awal["client_secret"] not in cred.secret_hash

    async def test_jawaban_tidak_boleh_disimpan_cache(self, users, client) -> None:
        h = await _login(client)
        awal = await _onboard(client, h)
        r = await client.get(f"/api/v1/ships/{awal['ship_id']}/credential", headers=h)
        assert "no-store" in r.headers.get("cache-control", "")

    async def test_dibuka_dicatat_di_audit_tanpa_rahasia(self, users, client) -> None:
        h = await _login(client)
        awal = await _onboard(client, h)
        await client.get(f"/api/v1/ships/{awal['ship_id']}/credential", headers=h)

        audit = await client.get("/api/v1/audit", headers=h)
        assert "fleet.credential_viewed" in audit.text
        assert awal["client_secret"] not in audit.text

    async def test_kredensial_lama_tanpa_salinan_terenkripsi_tidak_bisa_dibuka(
        self, users, client, session
    ) -> None:
        """Kapal yang didaftarkan sebelum fitur ini hanya punya hash. Jawabannya
        harus jelas supaya dashboard bisa menawarkan penerbitan ulang."""
        h = await _login(client)
        awal = await _onboard(client, h)
        cred = (
            await session.execute(
                select(DeviceCredential).where(DeviceCredential.client_id == awal["client_id"])
            )
        ).scalar_one()
        cred.secret_encrypted = None
        await session.commit()

        r = await client.get(f"/api/v1/ships/{awal['ship_id']}/credential", headers=h)
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "fleet.credential_not_recoverable"

    async def test_kredensial_yang_dicabut_tidak_dibuka(self, users, client) -> None:
        h = await _login(client)
        awal = await _onboard(client, h)
        await client.post(f"/api/v1/credentials/{awal['client_id']}/revoke", headers=h)

        r = await client.get(f"/api/v1/ships/{awal['ship_id']}/credential", headers=h)
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "fleet.no_active_credential"

    async def test_pengamat_ditolak_dengan_403(self, users, client) -> None:
        awal = await _onboard(client, await _login(client))
        h = await _login(client, "pengamat")
        r = await client.get(f"/api/v1/ships/{awal['ship_id']}/credential", headers=h)
        assert r.status_code == 403

    async def test_kapal_tidak_ada(self, users, client) -> None:
        h = await _login(client)
        r = await client.get(
            "/api/v1/ships/00000000-0000-0000-0000-000000000000/credential", headers=h
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "fleet.ship_not_found"


class TestTerbitkanKredensialKapal:
    async def test_menghasilkan_kredensial_baru_yang_bisa_dibuka_lagi(
        self, users, client, session
    ) -> None:
        h = await _login(client)
        awal = await _onboard(client, h)
        # Kapal lama: hanya hash, tidak bisa dibuka.
        lama = (
            await session.execute(
                select(DeviceCredential).where(DeviceCredential.client_id == awal["client_id"])
            )
        ).scalar_one()
        lama.secret_encrypted = None
        await session.commit()

        r = await client.post(f"/api/v1/ships/{awal['ship_id']}/credential", headers=h)
        assert r.status_code == 201, r.text
        baru = r.json()["data"]
        assert baru["client_id"] != awal["client_id"]
        assert await _device_login(client, baru) == 200

        lagi = (await client.get(f"/api/v1/ships/{awal['ship_id']}/credential", headers=h)).json()[
            "data"
        ]
        assert lagi["client_id"] == baru["client_id"]
        assert lagi["client_secret"] == baru["client_secret"]

    async def test_kredensial_lama_tidak_otomatis_dicabut(self, users, client) -> None:
        """Perangkat lama mungkin masih menyetor sisa backlog-nya."""
        h = await _login(client)
        awal = await _onboard(client, h)
        await client.post(f"/api/v1/ships/{awal['ship_id']}/credential", headers=h)
        assert await _device_login(client, awal) == 200

    async def test_pengamat_ditolak_dengan_403(self, users, client) -> None:
        awal = await _onboard(client, await _login(client))
        h = await _login(client, "pengamat")
        r = await client.post(f"/api/v1/ships/{awal['ship_id']}/credential", headers=h)
        assert r.status_code == 403


class TestUbahKapal:
    async def test_nama_dan_imo_berubah(self, users, client) -> None:
        h = await _login(client)
        awal = await _onboard(client, h)
        r = await client.patch(
            f"/api/v1/ships/{awal['ship_id']}",
            json={"name": "KM Nama Baru", "imo_number": "IMO9074729"},
            headers=h,
        )
        assert r.status_code == 200, r.text

        detail = (await client.get(f"/api/v1/ships/{awal['ship_id']}", headers=h)).json()["data"]
        assert detail["name"] == "KM Nama Baru"
        assert detail["imo_number"] == "IMO9074729"

    async def test_kode_kapal_tidak_bisa_diganti(self, users, client) -> None:
        """Dipakai sebagai nama folder ekspor USB."""
        h = await _login(client)
        awal = await _onboard(client, h)
        await client.patch(
            f"/api/v1/ships/{awal['ship_id']}",
            json={"name": "KM Lain", "slug": "SHIP-HACK"},
            headers=h,
        )
        detail = (await client.get(f"/api/v1/ships/{awal['ship_id']}", headers=h)).json()["data"]
        assert detail["slug"] == awal["slug"]

    async def test_hanya_yang_dikirim_yang_berubah(self, users, client) -> None:
        h = await _login(client)
        awal = await _onboard(client, h, imo_number="IMO1")
        await client.patch(f"/api/v1/ships/{awal['ship_id']}", json={"name": "KM Baru"}, headers=h)
        detail = (await client.get(f"/api/v1/ships/{awal['ship_id']}", headers=h)).json()["data"]
        assert detail["imo_number"] == "IMO1"

    async def test_imo_bisa_dikosongkan(self, users, client) -> None:
        h = await _login(client)
        awal = await _onboard(client, h, imo_number="IMO1")
        await client.patch(f"/api/v1/ships/{awal['ship_id']}", json={"imo_number": ""}, headers=h)
        detail = (await client.get(f"/api/v1/ships/{awal['ship_id']}", headers=h)).json()["data"]
        assert detail["imo_number"] is None

    async def test_imo_bentrok_ditolak_dengan_pesan_jelas(self, users, client) -> None:
        h = await _login(client)
        await _onboard(client, h, slug="SHIP-701", imo_number="IMO7")
        kedua = await _onboard(client, h, slug="SHIP-702")
        r = await client.patch(
            f"/api/v1/ships/{kedua['ship_id']}", json={"imo_number": "IMO7"}, headers=h
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "fleet.imo_taken"

    async def test_nama_kosong_ditolak(self, users, client) -> None:
        h = await _login(client)
        awal = await _onboard(client, h)
        r = await client.patch(f"/api/v1/ships/{awal['ship_id']}", json={"name": ""}, headers=h)
        assert r.status_code == 422

    async def test_perubahan_dicatat_di_audit(self, users, client) -> None:
        h = await _login(client)
        awal = await _onboard(client, h)
        await client.patch(f"/api/v1/ships/{awal['ship_id']}", json={"name": "KM Audit"}, headers=h)
        assert "fleet.ship_updated" in (await client.get("/api/v1/audit", headers=h)).text

    async def test_pengamat_ditolak_dengan_403(self, users, client) -> None:
        awal = await _onboard(client, await _login(client))
        h = await _login(client, "pengamat")
        r = await client.patch(f"/api/v1/ships/{awal['ship_id']}", json={"name": "X"}, headers=h)
        assert r.status_code == 403


class TestNonaktifkanKapal:
    """'Cabut aksesnya, simpan datanya' (docs/operations/08-ship-decommissioning.md)."""

    async def _decommission(self, client, headers, ship_id: str):
        return await client.post(f"/api/v1/ships/{ship_id}/decommission", headers=headers)

    async def test_kapal_hilang_dari_daftar_bawaan(self, users, client) -> None:
        h = await _login(client)
        awal = await _onboard(client, h)
        assert (await self._decommission(client, h, awal["ship_id"])).status_code == 200

        ships = (await client.get("/api/v1/ships", headers=h)).json()["data"]
        assert awal["ship_id"] not in {s["ship_id"] for s in ships}

    async def test_masih_bisa_dilihat_bila_diminta(self, users, client) -> None:
        h = await _login(client)
        awal = await _onboard(client, h)
        await self._decommission(client, h, awal["ship_id"])

        ships = (await client.get("/api/v1/ships?include_inactive=true", headers=h)).json()["data"]
        row = next(s for s in ships if s["ship_id"] == awal["ship_id"])
        assert row["is_active"] is False

    async def test_akses_perangkat_ditutup(self, users, client) -> None:
        h = await _login(client)
        awal = await _onboard(client, h)
        assert await _device_login(client, awal) == 200

        r = await self._decommission(client, h, awal["ship_id"])
        assert awal["client_id"] in r.json()["data"]["revoked_credentials"]
        assert await _device_login(client, awal) == 401

    async def test_semua_kredensial_ikut_dicabut(self, users, client) -> None:
        h = await _login(client)
        awal = await _onboard(client, h)
        baru = (await client.post(f"/api/v1/ships/{awal['ship_id']}/credential", headers=h)).json()[
            "data"
        ]
        await self._decommission(client, h, awal["ship_id"])
        assert await _device_login(client, awal) == 401
        assert await _device_login(client, baru) == 401

    async def test_baris_kapal_dan_riwayatnya_tidak_dihapus(self, users, client, session) -> None:
        h = await _login(client)
        awal = await _onboard(client, h)
        await self._decommission(client, h, awal["ship_id"])

        ship_id = UUID(awal["ship_id"])
        session.expire_all()
        ship = await session.get(Ship, ship_id)
        assert ship is not None and ship.is_active is False
        assert await session.get(ShipSyncState, ship_id) is not None
        devices = (await session.scalars(select(Device).where(Device.ship_id == ship_id))).all()
        assert len(devices) == 1
        creds = (
            await session.scalars(
                select(DeviceCredential).where(DeviceCredential.ship_id == ship_id)
            )
        ).all()
        assert creds and all(c.revoked_at is not None for c in creds)

    async def test_diulang_tidak_error(self, users, client) -> None:
        h = await _login(client)
        awal = await _onboard(client, h)
        assert (await self._decommission(client, h, awal["ship_id"])).status_code == 200
        assert (await self._decommission(client, h, awal["ship_id"])).status_code == 200

    async def test_kode_kapal_tetap_terpakai(self, users, client) -> None:
        """Dipakai di nama folder ekspor dan ledger; tidak boleh didaur ulang."""
        h = await _login(client)
        awal = await _onboard(client, h, slug="SHIP-710")
        await self._decommission(client, h, awal["ship_id"])
        r = await client.post(
            "/api/v1/ships/onboard", json={"name": "KM Lain", "slug": "SHIP-710"}, headers=h
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "fleet.slug_taken"

    async def test_dicatat_di_audit(self, users, client) -> None:
        h = await _login(client)
        awal = await _onboard(client, h)
        await self._decommission(client, h, awal["ship_id"])
        assert "fleet.ship_decommissioned" in (await client.get("/api/v1/audit", headers=h)).text

    async def test_pengamat_ditolak_dengan_403(self, users, client) -> None:
        awal = await _onboard(client, await _login(client))
        h = await _login(client, "pengamat")
        assert (await self._decommission(client, h, awal["ship_id"])).status_code == 403
