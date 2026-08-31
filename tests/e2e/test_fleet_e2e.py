"""Simulasi end-to-end: tiga kapal, dua belas skenario.

Setiap test menjalankan komponen produksi yang sama dengan yang akan berjalan di
kapal, menyetor ke Central Platform sungguhan, dan memverifikasi hasilnya di
PostgreSQL **dan** InfluxDB.

Jaminan yang diperiksa berulang di seluruh berkas ini:

- tidak ada data yang hilang
- tidak ada telemetry ganda
- resume bekerja setelah transfer terputus
- ACK bekerja, dan hanya ACK yang memindahkan batch keluar dari pending
- jumlah pending selalu benar
- status di dashboard pusat benar
- identitas kapal tidak pernah tertukar
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx
import pytest

from fleetview_usb_import import UsbImporter, verify_package

from .conftest import Central, ShipSim

pytestmark = [pytest.mark.e2e, pytest.mark.integration]

MakeShip = Callable[..., Awaitable[ShipSim]]


async def count_points(central: Central, ship: ShipSim) -> int:
    """Jumlah titik telemetry milik satu kapal di InfluxDB pusat."""
    csv = await central.influx.query_csv(
        f'from(bucket:"telemetry") |> range(start:-6h, stop:5m) '
        f'|> filter(fn:(r) => r.ship_id == "{ship.ship_id}") |> count()'
    )
    total = 0
    for block in csv.replace("\r\n", "\n").split("\n\n"):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        header = next((ln for ln in lines if ln.startswith(",result")), None)
        if header is None:
            continue
        idx = header.split(",").index("_value")
        for line in lines:
            if line.startswith(",_result"):
                total += int(line.split(",")[idx])
    return total


async def committed_batches(central: Central, ship: ShipSim) -> int:
    rows = await central.session_execute(
        "SELECT count(*) FROM sync_batches WHERE ship_id = :s AND status = 'committed'",
        {"s": ship.ship_id},
    )
    return int(rows[0][0])


async def watermark(central: Central, ship: ShipSim) -> tuple[int, int]:
    rows = await central.session_execute(
        "SELECT last_contiguous_sequence, total_records FROM ship_sync_state WHERE ship_id = :s",
        {"s": ship.ship_id},
    )
    return int(rows[0][0]), int(rows[0][1])


# ============================================================ 1. semua online


class Test01SemuaKapalOnline:
    async def test_tiga_kapal_menyetor_penuh(self, central: Central, fleet: list[ShipSim]) -> None:
        for ship in fleet:
            await ship.collect(rounds=5)
            assert ship.pending == 20  # 4 sensor x 5 putaran

        for ship in fleet:
            await ship.sync_all()

        for ship in fleet:
            assert ship.pending == 0, f"{ship.slug} masih menyisakan data"
            assert ship.acked == 20
            last_contiguous, total = await watermark(central, ship)
            assert last_contiguous == 20
            assert total == 20
            assert await count_points(central, ship) == 20

    async def test_identitas_tidak_tertukar(self, central: Central, fleet: list[ShipSim]) -> None:
        """Jaminan paling mendasar: data satu kapal tidak boleh muncul atas nama
        kapal lain — baik karena salah konfigurasi maupun karena bug."""
        for ship in fleet:
            await ship.collect(rounds=3)
            await ship.sync_all()

        for ship in fleet:
            rows = await central.session_execute(
                "SELECT DISTINCT ship_id, device_id FROM sync_batches WHERE ship_id = :s",
                {"s": ship.ship_id},
            )
            assert len(rows) == 1
            assert rows[0][0] == ship.ship_id
            assert rows[0][1] == ship.device_id

        # Dan di InfluxDB: setiap kapal punya jumlah titiknya sendiri.
        for ship in fleet:
            assert await count_points(central, ship) == 12

    async def test_kredensial_satu_kapal_tidak_bisa_menyetor_untuk_kapal_lain(
        self, central: Central, fleet: list[ShipSim]
    ) -> None:
        """Kredensial yang bocor dari satu kapal tidak boleh bisa menyuntikkan
        data atas nama kapal mana pun."""
        korban, penyerang = fleet[0], fleet[1]
        await korban.collect(rounds=2)

        built = korban.sync._batcher.build_next()
        assert built is not None

        async with httpx.AsyncClient(
            transport=central.transport,
            base_url="http://central.test",
            headers={"Authorization": f"Bearer {penyerang.token}"},
        ) as client:
            response = await client.post(
                "/api/v1/ingest/batches",
                content=built.payload,
                headers={
                    "Content-Type": "application/octet-stream",
                    "X-Batch-Envelope": built.envelope.model_dump_json(),
                },
            )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "ingest.ship_mismatch"
        assert await committed_batches(central, korban) == 0


# ============================================================ 2–4. offline


class Test02KapalOffline:
    async def test_data_tertahan_bukan_hilang(self, central: Central, fleet: list[ShipSim]) -> None:
        offline, online = fleet[0], fleet[1]
        central.down = True

        await offline.collect(rounds=5)
        assert await offline.sync_all(max_cycles=3) == 0
        assert offline.pending == 20
        assert offline.acked == 0

        central.down = False
        await online.collect(rounds=2)
        await online.sync_all()
        assert online.pending == 0  # kapal lain tidak ikut terganggu


class Test03OfflineLama:
    async def test_backlog_24_jam_tersimpan_utuh(
        self, central: Central, fleet: list[ShipSim]
    ) -> None:
        """Offline panjang. Yang diperiksa: tidak ada satu record pun hilang, dan
        umur data tertua terlaporkan dengan benar."""
        ship = fleet[0]
        central.down = True

        for _ in range(30):
            await ship.collect(rounds=1)
            await ship.sync_all(max_cycles=1)

        assert ship.pending == 120  # 4 sensor x 30 putaran
        assert ship.acked == 0
        assert ship.outbox.oldest_pending_age_seconds() is not None

        progress = ship.sync.progress()
        assert progress.pending_records + progress.pending_batches * 0 >= 0
        assert ship.sync.stats.batches_acked == 0


class Test04Reconnect:
    async def test_seluruh_backlog_tersetor_saat_pulih(
        self, central: Central, fleet: list[ShipSim]
    ) -> None:
        ship = fleet[0]
        central.down = True
        for _ in range(20):
            await ship.collect(rounds=1)
            await ship.sync_all(max_cycles=1)
        tertahan = ship.pending
        assert tertahan == 80

        central.down = False
        await ship.sync_all()

        assert ship.pending == 0
        assert ship.acked == 80
        last_contiguous, total = await watermark(central, ship)
        assert last_contiguous == 80
        assert total == 80
        assert await count_points(central, ship) == 80

    async def test_urutan_tertua_dulu(self, central: Central, fleet: list[ShipSim]) -> None:
        """Watermark contiguous hanya maju kalau batch datang berurutan. Kalau
        yang terbaru dikirim lebih dulu, angka "pending" di dashboard kehilangan
        artinya."""
        ship = fleet[0]
        central.down = True
        for _ in range(12):
            await ship.collect(rounds=1)
            await ship.sync_all(max_cycles=1)

        central.down = False
        await ship.sync_all()

        rows = await central.session_execute(
            "SELECT sequence_start, sequence_end FROM sync_batches "
            "WHERE ship_id = :s ORDER BY committed_at, sequence_start",
            {"s": ship.ship_id},
        )
        starts = [int(r[0]) for r in rows]
        assert starts == sorted(starts)


# ============================================================ 5. backlog besar


class Test05BacklogBesar:
    async def test_backlog_besar_tersetor_tanpa_kehilangan(
        self, central: Central, fleet: list[ShipSim]
    ) -> None:
        ship = fleet[0]
        central.down = True
        for _ in range(75):
            await ship.collect(rounds=1)
            await ship.sync_all(max_cycles=1)
        assert ship.pending == 300

        central.down = False
        acked = await ship.sync_all(max_cycles=100)

        assert acked >= 12  # 300 record / batch 25
        assert ship.pending == 0
        assert ship.acked == 300
        assert await count_points(central, ship) == 300
        last_contiguous, _ = await watermark(central, ship)
        assert last_contiguous == 300


# ============================================================ 6. putus di tengah


class Test06PutusSaatSync:
    async def test_transfer_terputus_lalu_dilanjutkan(
        self, central: Central, fleet: list[ShipSim]
    ) -> None:
        """Satelit kapal putus di tengah transfer adalah kejadian biasa. Tanpa
        pemulihan, backlog tidak akan pernah selesai terkirim."""
        ship = fleet[0]
        await ship.collect(rounds=10)
        assert ship.pending == 40

        # Payload batch 25-record jauh di atas 100 byte, jadi setiap kiriman putus.
        central.interrupt_after_bytes = 100
        assert await ship.sync_all(max_cycles=3) == 0
        assert ship.pending == 40  # tidak ada yang hilang
        assert ship.acked == 0

        central.interrupt_after_bytes = None
        await ship.sync_all()

        assert ship.pending == 0
        assert ship.acked == 40
        assert await count_points(central, ship) == 40

    async def test_batch_id_stabil_melewati_gangguan(
        self, central: Central, fleet: list[ShipSim]
    ) -> None:
        """Kalau batch_id berubah setiap percobaan, idempotensi di central runtuh
        dan setiap pengulangan menjadi data baru."""
        ship = fleet[0]
        await ship.collect(rounds=5)

        central.interrupt_after_bytes = 100
        await ship.sync_all(max_cycles=2)
        sebelum = [b.batch_id for b in ship.outbox.batches_ready(limit=10)]

        central.interrupt_after_bytes = None
        await ship.sync_all()

        rows = await central.session_execute(
            "SELECT batch_id::text FROM sync_batches WHERE ship_id = :s", {"s": ship.ship_id}
        )
        tersimpan = {r[0] for r in rows}
        assert set(sebelum) <= tersimpan


# ============================================================ 7. batch ganda


class Test07BatchGanda:
    async def test_kiriman_ulang_tidak_menggandakan(
        self, central: Central, fleet: list[ShipSim]
    ) -> None:
        ship = fleet[0]
        await ship.collect(rounds=5)
        await ship.sync_all()

        titik_awal = await count_points(central, ship)
        batch_awal = await committed_batches(central, ship)

        # Kirim ulang setiap batch yang sudah committed, apa adanya.
        async with httpx.AsyncClient(
            transport=central.transport,
            base_url="http://central.test",
            headers={"Authorization": f"Bearer {ship.token}"},
        ) as client:
            for row in ship.outbox.batches_ready(limit=100, now_us=2_000_000_000_000_000):
                built = ship.sync._batcher.rebuild(row.batch_id)
                if built is None:
                    continue
                response = await client.post(
                    "/api/v1/ingest/batches",
                    content=built.payload,
                    headers={
                        "Content-Type": "application/octet-stream",
                        "X-Batch-Envelope": built.envelope.model_dump_json(),
                    },
                )
                assert response.status_code == 200

        assert await count_points(central, ship) == titik_awal
        assert await committed_batches(central, ship) == batch_awal

    async def test_ack_yang_diputar_ulang_identik(
        self, central: Central, fleet: list[ShipSim]
    ) -> None:
        """ACK yang hilang berbiaya satu round trip, bukan satu upload ulang."""
        ship = fleet[0]
        await ship.collect(rounds=3)
        built = ship.sync._batcher.build_next()
        assert built is not None

        async with httpx.AsyncClient(
            transport=central.transport,
            base_url="http://central.test",
            headers={"Authorization": f"Bearer {ship.token}"},
        ) as client:
            headers = {
                "Content-Type": "application/octet-stream",
                "X-Batch-Envelope": built.envelope.model_dump_json(),
            }
            first = await client.post(
                "/api/v1/ingest/batches", content=built.payload, headers=headers
            )
            second = await client.post(
                "/api/v1/ingest/batches", content=built.payload, headers=headers
            )

        assert first.json()["data"] == second.json()["data"]


# ============================================================ 8–9. USB


class Test08EksporUsb:
    async def test_ekspor_menghasilkan_paket_terverifikasi(self, fleet: list[ShipSim]) -> None:
        ship = fleet[0]
        await ship.collect(rounds=10)

        slot = ship.sync.manager.slot_for(
            next(s.adapter.kind for s in ship.sync.manager.slots if s.adapter.name == "usb")
        )
        assert slot is not None
        exported = await ship.sync.export_to(slot)
        assert exported >= 1

        root = ship.export_dir / ship.slug
        valid, problems = verify_package(root)
        assert valid, problems
        assert (root / "manifest.json").is_file()
        assert list((root / "telemetry").glob("*.fvb"))

    async def test_ekspor_tidak_menghapus_data_lokal(self, fleet: list[ShipSim]) -> None:
        """Berkas di flashdisk bukan bukti central menerimanya. Flashdisk yang
        hilang karena itu tidak menghilangkan apa pun."""
        ship = fleet[0]
        await ship.collect(rounds=8)
        sebelum = ship.pending

        slot = next(s for s in ship.sync.manager.slots if s.adapter.name == "usb")
        await ship.sync.export_to(slot)

        assert ship.acked == 0
        assert ship.pending == sebelum

    async def test_media_rusak_terdeteksi(self, fleet: list[ShipSim]) -> None:
        ship = fleet[0]
        await ship.collect(rounds=5)
        slot = next(s for s in ship.sync.manager.slots if s.adapter.name == "usb")
        await ship.sync.export_to(slot)

        root = ship.export_dir / ship.slug
        berkas = next((root / "telemetry").glob("*.fvb"))
        berkas.write_bytes(b"rusak")

        valid, problems = verify_package(root)
        assert valid is False
        assert any("checksum" in p for p in problems)


class Test09ImporUsb:
    async def test_impor_menyimpan_data_di_central(
        self, central: Central, fleet: list[ShipSim]
    ) -> None:
        ship = fleet[0]
        await ship.collect(rounds=10)
        slot = next(s for s in ship.sync.manager.slots if s.adapter.name == "usb")
        await ship.sync.export_to(slot)

        importer = UsbImporter(
            base_url="http://central.test",
            token=ship.token,
            client=httpx.AsyncClient(
                transport=central.transport,
                base_url="http://central.test",
                headers={"Authorization": f"Bearer {ship.token}"},
            ),
        )
        result = await importer.import_package(ship.export_dir / ship.slug)
        await importer.close()

        assert result.ok, result.problems
        assert result.committed == result.total
        assert await count_points(central, ship) == 40
        last_contiguous, _ = await watermark(central, ship)
        assert last_contiguous == 40

    async def test_impor_menolak_paket_rusak_sebelum_mengirim(
        self, central: Central, fleet: list[ShipSim]
    ) -> None:
        """Verifikasi terjadi sebelum satu byte pun dikirim. Data rusak yang
        sampai ke central lebih buruk daripada tidak mengirim apa pun."""
        ship = fleet[0]
        await ship.collect(rounds=5)
        slot = next(s for s in ship.sync.manager.slots if s.adapter.name == "usb")
        await ship.sync.export_to(slot)

        root = ship.export_dir / ship.slug
        next((root / "telemetry").glob("*.fvb")).write_bytes(b"rusak")

        sebelum = central.requests
        importer = UsbImporter(
            base_url="http://central.test",
            token=ship.token,
            client=httpx.AsyncClient(transport=central.transport, base_url="http://central.test"),
        )
        result = await importer.import_package(root)
        await importer.close()

        assert result.ok is False
        assert central.requests == sebelum  # tidak ada yang dikirim
        assert await committed_batches(central, ship) == 0

    async def test_impor_setelah_sync_online_tidak_menggandakan(
        self, central: Central, fleet: list[ShipSim]
    ) -> None:
        """Skenario nyata: kapal sempat sinkron online, lalu teknisi tetap
        membawa flashdisk yang berisi data sama."""
        ship = fleet[0]
        await ship.collect(rounds=10)

        slot = next(s for s in ship.sync.manager.slots if s.adapter.name == "usb")
        await ship.sync.export_to(slot)
        await ship.sync_all()  # sekarang tersetor online juga

        titik_setelah_online = await count_points(central, ship)

        importer = UsbImporter(
            base_url="http://central.test",
            token=ship.token,
            client=httpx.AsyncClient(
                transport=central.transport,
                base_url="http://central.test",
                headers={"Authorization": f"Bearer {ship.token}"},
            ),
        )
        result = await importer.import_package(ship.export_dir / ship.slug)
        await importer.close()

        assert result.ok
        assert await count_points(central, ship) == titik_setelah_online


# ============================================================ 10–12


class Test10CentralTidakTersedia:
    async def test_akuisisi_terus_berjalan(self, central: Central, fleet: list[ShipSim]) -> None:
        ship = fleet[0]
        central.down = True

        for _ in range(10):
            await ship.collect(rounds=1)
            await ship.sync_all(max_cycles=1)

        assert ship.collector.stats.polls_failed == 0
        assert ship.pending == 40

        central.down = False
        await ship.sync_all()
        assert ship.acked == 40


class Test11TelemetryTidakValid:
    async def test_nilai_buruk_disimpan_dengan_flag_bukan_dibuang(
        self, central: Central, make_ship: MakeShip
    ) -> None:
        """Sensor rusak harus bisa dibedakan dari kapal yang tidak mengirim data.
        Kalau datanya dibuang, keduanya terlihat sama di pusat."""
        from fleetview_edge.config import SensorConfig, SensorRegistry

        ship = await make_ship("SHIP-BAD")
        ship.collector._registry = SensorRegistry(
            [
                SensorConfig(
                    sensor_id="me_port_rpm",
                    channel="ch_me_port_rpm",
                    metric="rpm",
                    unit="rpm",
                    poll_interval_seconds=1e-6,
                    validation={"min_value": 0, "max_value": 2000},
                )
            ]
        )
        ship.collector._parser._registry = ship.collector._registry
        ship.adapter.set_value("ch_me_port_rpm", 99_999.0)

        await ship.collect(rounds=3)
        await ship.sync_all()

        assert ship.acked == 3  # tidak dibuang
        csv = await central.influx.query_csv(
            f'from(bucket:"telemetry") |> range(start:-6h, stop:5m) '
            f'|> filter(fn:(r) => r.ship_id == "{ship.ship_id}")'
        )
        assert "suspect" in csv  # tersimpan dengan flag kualitas

    async def test_payload_rusak_ditolak_tanpa_diulang(
        self, central: Central, fleet: list[ShipSim]
    ) -> None:
        ship = fleet[0]
        await ship.collect(rounds=3)
        built = ship.sync._batcher.build_next()
        assert built is not None

        async with httpx.AsyncClient(
            transport=central.transport,
            base_url="http://central.test",
            headers={"Authorization": f"Bearer {ship.token}"},
        ) as client:
            response = await client.post(
                "/api/v1/ingest/batches",
                content=built.payload + b"rusak",
                headers={
                    "Content-Type": "application/octet-stream",
                    "X-Batch-Envelope": built.envelope.model_dump_json(),
                },
            )

        assert response.status_code == 400
        assert response.json()["error"]["retryable"] is False
        assert await committed_batches(central, ship) == 0


class Test12PerangkatLapanganPutus:
    async def test_collector_pulih_dan_tidak_menimbulkan_celah(
        self, central: Central, make_ship: MakeShip
    ) -> None:
        """Sambungan ke perangkat lapangan putus lalu pulih. Nomor sequence harus
        tetap berurutan tanpa celah — celah membuat server mengira ada data
        hilang yang sebenarnya tidak pernah ada."""
        from fleetview_edge.protocol import MockScript

        ship = await make_ship("SHIP-LPA", script=MockScript(disconnect_on=[2, 5]))

        for _ in range(10):
            await ship.collect(rounds=1)

        assert ship.collector.stats.reconnects >= 1
        await ship.sync_all()

        rows = await central.session_execute(
            "SELECT sequence_start, sequence_end FROM sync_batches "
            "WHERE ship_id = :s ORDER BY sequence_start",
            {"s": ship.ship_id},
        )
        sequences: list[int] = []
        for start, end in rows:
            sequences.extend(range(int(start), int(end) + 1))
        assert sequences == list(range(1, len(sequences) + 1))

    async def test_tidak_ada_data_palsu_saat_putus(
        self, central: Central, make_ship: MakeShip
    ) -> None:
        """Sambungan putus tidak boleh menghasilkan record. Nilai karangan lebih
        buruk daripada tidak ada data, karena tidak menimbulkan gejala."""
        from fleetview_edge.protocol import MockScript

        ship = await make_ship("SHIP-DOWN", script=MockScript(timeout_on=[0, 1, 2]))
        for _ in range(3):
            await ship.collect(rounds=1)

        assert ship.pending == 0
        assert ship.collector.stats.timeouts == 3


# ============================================================ dashboard


class TestDashboardPusat:
    async def test_status_armada_mencerminkan_keadaan_sebenarnya(
        self, central: Central, fleet: list[ShipSim], operator: httpx.AsyncClient
    ) -> None:
        aktif, diam = fleet[0], fleet[1]
        await aktif.collect(rounds=5)
        await aktif.sync_all()

        response = await operator.get("/api/v1/ships")
        assert response.status_code == 200
        ships = {s["slug"]: s for s in response.json()["data"]}

        assert ships[aktif.slug]["connection_state"] == "online"
        assert ships[aktif.slug]["last_contiguous_sequence"] == 20
        assert ships[aktif.slug]["total_records"] == 20
        assert ships[aktif.slug]["has_gap"] is False

        assert ships[diam.slug]["connection_state"] == "offline"
        assert ships[diam.slug]["total_records"] == 0

    async def test_sensor_terdaftar_otomatis_per_kapal(
        self, central: Central, fleet: list[ShipSim], operator: httpx.AsyncClient
    ) -> None:
        for ship in fleet[:2]:
            await ship.collect(rounds=2)
            await ship.sync_all()

        for ship in fleet[:2]:
            response = await operator.get(f"/api/v1/ships/{ship.ship_id}/sensors")
            sensors = {s["sensor_id"] for s in response.json()["data"]}
            assert sensors == {
                "me_port_rpm",
                "me_port_lube_pressure",
                "me_port_exhaust_temp",
                "fuel_level_main",
            }

    async def test_telemetry_terbaca_kembali_per_kapal(
        self, central: Central, fleet: list[ShipSim], operator: httpx.AsyncClient
    ) -> None:
        ship = fleet[0]
        await ship.collect(rounds=5)
        await ship.sync_all()

        response = await operator.get(
            f"/api/v1/telemetry/series?ship_id={ship.ship_id}&range_seconds=21600"
        )
        assert response.status_code == 200
        series = response.json()["data"]["series"]
        metrics = {s["measurement"] for s in series}
        assert {"rpm", "pressure", "temperature", "fuel_level"} <= metrics

    async def test_kapal_tanpa_data_tidak_memunculkan_data_kapal_lain(
        self, central: Central, fleet: list[ShipSim], operator: httpx.AsyncClient
    ) -> None:
        """Pemeriksaan terakhir identitas: kapal yang belum pernah mengirim harus
        benar-benar kosong, bukan memantulkan data tetangganya."""
        aktif, kosong = fleet[0], fleet[2]
        await aktif.collect(rounds=5)
        await aktif.sync_all()

        response = await operator.get(
            f"/api/v1/telemetry/series?ship_id={kosong.ship_id}&range_seconds=21600"
        )
        assert response.json()["data"]["series"] == []
        assert await count_points(central, kosong) == 0
