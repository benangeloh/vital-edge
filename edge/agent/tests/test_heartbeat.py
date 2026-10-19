"""Test pengiriman heartbeat dari kapal ke central.

Heartbeat adalah satu-satunya cara central tahu kondisi perangkat kapal (disk,
CPU, status akuisisi). Sebelumnya endpoint dan kontraknya ada, tetapi tidak ada
yang mengirimnya, sehingga dashboard tidak pernah punya data kesehatan.

Aturan yang diuji di sini: heartbeat tidak boleh menjatuhkan agent, dan tidak
boleh mengarang angka yang tidak terbaca.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest

from fleetview_common import now_micros, now_utc
from fleetview_contracts import Heartbeat
from fleetview_edge.sync import (
    HeartbeatBuilder,
    HeartbeatSender,
    NetworkManager,
    NetworkTransport,
    SyncProgress,
    TransportRejectedError,
    TransportSlot,
    TransportUnavailableError,
)

SHIP = UUID("11111111-1111-1111-1111-111111111111")
DEVICE = UUID("22222222-2222-2222-2222-222222222222")


def _response_body(*, skew_seconds: float = 0.0) -> dict[str, object]:
    return {
        "ok": True,
        "data": {
            "server_time": (now_utc() - timedelta(seconds=skew_seconds)).isoformat(),
            "config_version": None,
            "commands": [],
        },
        "error": None,
        "meta": {},
    }


def _transport(handler) -> NetworkTransport:  # type: ignore[no-untyped-def]
    return NetworkTransport(
        base_url="http://central.test",
        token="t",
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://central.test"
        ),
    )


def _heartbeat(**over: object) -> Heartbeat:
    base: dict[str, object] = {
        "ship_id": SHIP,
        "device_id": DEVICE,
        "sent_at": now_utc(),
        "agent_version": "0.1.0",
        "config_version": "cfg-1",
    }
    return Heartbeat(**{**base, **over})  # type: ignore[arg-type]


class TestSendHeartbeat:
    async def test_mengirim_ke_endpoint_heartbeat_dan_mengurai_jawaban(self) -> None:
        seen: list[httpx.Request] = []

        def handler(r: httpx.Request) -> httpx.Response:
            seen.append(r)
            return httpx.Response(200, json=_response_body())

        response = await _transport(handler).send_heartbeat(_heartbeat())

        assert seen[0].method == "POST"
        assert seen[0].url.path == "/api/v1/ingest/heartbeat"
        assert json.loads(seen[0].content)["ship_id"] == str(SHIP)
        assert response.commands == []

    async def test_5xx_bisa_diulang(self) -> None:
        transport = _transport(lambda _r: httpx.Response(503))
        with pytest.raises(TransportUnavailableError):
            await transport.send_heartbeat(_heartbeat())

    async def test_4xx_ditolak_permanen(self) -> None:
        transport = _transport(lambda _r: httpx.Response(422, json={"ok": False}))
        with pytest.raises(TransportRejectedError):
            await transport.send_heartbeat(_heartbeat())

    async def test_jawaban_tidak_bisa_diurai_ditolak(self) -> None:
        transport = _transport(
            lambda _r: httpx.Response(200, json={"data": {"bukan": "heartbeat"}})
        )
        with pytest.raises(TransportRejectedError):
            await transport.send_heartbeat(_heartbeat())


@dataclass
class _Metrics:
    disk_percent: float | None = 62.5
    cpu: float | None = 12.0
    memory: float | None = 40.0
    uptime: int | None = 3600

    def disk(self) -> tuple[float | None, int | None]:
        return self.disk_percent, 1_000_000

    def cpu_percent(self) -> float | None:
        return self.cpu

    def memory_percent(self) -> float | None:
        return self.memory

    def uptime_seconds(self) -> int | None:
        return self.uptime


class _Collector:
    def __init__(self, *, connected: bool = True, link: str = "connected") -> None:
        self._health = {"connected": connected, "link": link}
        self.latest_readings = {"rpm": SimpleNamespace(timestamp=now_micros())}

    async def snapshot_health(self) -> dict[str, object]:
        return dict(self._health)


class _Storage:
    def __init__(self, *, healthy: bool = True) -> None:
        self._healthy = healthy

    async def health(self) -> SimpleNamespace:
        return SimpleNamespace(healthy=self._healthy)


def _progress(**over: object) -> SyncProgress:
    base: dict[str, object] = {
        "pending_records": 7,
        "pending_batches": 2,
        "oldest_pending_age_seconds": 41.6,
        "active_transport": "lan",
        "last_success_us": None,
        "last_error": None,
        "quarantined_batches": 0,
    }
    return SyncProgress(**{**base, **over})  # type: ignore[arg-type]


def _builder(
    *,
    metrics: _Metrics | None = None,
    collector: _Collector | None = None,
    storage: _Storage | None = None,
    progress: SyncProgress | None = None,
) -> HeartbeatBuilder:
    return HeartbeatBuilder(
        ship_id=SHIP,
        device_id=DEVICE,
        agent_version="0.1.0",
        config_version="cfg-1",
        metrics=metrics or _Metrics(),
        progress=lambda: progress or _progress(),
        collector=collector if collector is not None else _Collector(),
        storage=storage if storage is not None else _Storage(),
    )


class TestHeartbeatBuilder:
    async def test_identitas_dan_antrean(self) -> None:
        hb = await _builder().build(clock_skew_seconds=None)

        assert (hb.ship_id, hb.device_id) == (SHIP, DEVICE)
        assert (hb.agent_version, hb.config_version) == ("0.1.0", "cfg-1")
        assert hb.active_transport is not None
        assert hb.active_transport.value == "lan"
        assert (hb.pending_records, hb.pending_batches) == (7, 2)
        assert hb.oldest_pending_age_seconds == 42

    async def test_kesehatan_diisi_dari_metrik_sungguhan(self) -> None:
        hb = await _builder().build(clock_skew_seconds=0.4)

        assert hb.health is not None
        assert hb.health.disk_used_percent == 62.5
        assert hb.health.cpu_used_percent == 12.0
        assert hb.health.memory_used_percent == 40.0
        assert hb.health.uptime_seconds == 3600
        assert hb.health.collector_healthy is True
        assert hb.health.storage_healthy is True
        assert hb.health.protocol_link_healthy is True
        assert hb.health.clock_skew_seconds == 0.4

    async def test_cpu_dan_memori_tidak_terbaca_dikirim_none_bukan_nol(self) -> None:
        hb = await _builder(metrics=_Metrics(cpu=None, memory=None)).build(clock_skew_seconds=None)

        assert hb.health is not None
        assert hb.health.cpu_used_percent is None
        assert hb.health.memory_used_percent is None

    async def test_uptime_tidak_terbaca_dikirim_none_bukan_nol(self) -> None:
        hb = await _builder(metrics=_Metrics(uptime=None)).build(clock_skew_seconds=None)

        assert hb.health is not None
        assert hb.health.uptime_seconds is None

    async def test_disk_tidak_terbaca_berarti_tanpa_blok_kesehatan(self) -> None:
        """Lebih baik tidak melapor daripada melapor disk 0%."""
        hb = await _builder(metrics=_Metrics(disk_percent=None)).build(clock_skew_seconds=None)

        assert hb.health is None

    async def test_akuisisi_terputus_dilaporkan(self) -> None:
        hb = await _builder(collector=_Collector(connected=False, link="disconnected")).build(
            clock_skew_seconds=None
        )

        assert hb.health is not None
        assert hb.health.collector_healthy is False
        assert hb.health.protocol_link_healthy is False

    async def test_link_degraded_tidak_dihitung_sehat(self) -> None:
        hb = await _builder(collector=_Collector(link="degraded")).build(clock_skew_seconds=None)

        assert hb.health is not None
        assert hb.health.protocol_link_healthy is False

    async def test_penyimpanan_bermasalah_dilaporkan(self) -> None:
        hb = await _builder(storage=_Storage(healthy=False)).build(clock_skew_seconds=None)

        assert hb.health is not None
        assert hb.health.storage_healthy is False

    async def test_waktu_telemetry_terakhir_dari_record_terbaru(self) -> None:
        collector = _Collector()
        hb = await _builder(collector=collector).build(clock_skew_seconds=None)

        assert hb.last_telemetry_at is not None
        assert abs((now_utc() - hb.last_telemetry_at).total_seconds()) < 5

    async def test_tanpa_telemetry_waktunya_none(self) -> None:
        collector = _Collector()
        collector.latest_readings = {}
        hb = await _builder(collector=collector).build(clock_skew_seconds=None)

        assert hb.last_telemetry_at is None

    async def test_transport_aktif_tak_dikenal_menjadi_none(self) -> None:
        hb = await _builder(progress=_progress(active_transport="satelit")).build(
            clock_skew_seconds=None
        )

        assert hb.active_transport is None

    async def test_umur_antrean_tidak_diketahui_tetap_none(self) -> None:
        hb = await _builder(progress=_progress(oldest_pending_age_seconds=None)).build(
            clock_skew_seconds=None
        )

        assert hb.oldest_pending_age_seconds is None


class _Builder:
    """Pengganti HeartbeatBuilder yang mencatat panggilannya."""

    def __init__(self, *, fail: bool = False) -> None:
        self.skews: list[float | None] = []
        self._fail = fail

    async def build(self, *, clock_skew_seconds: float | None) -> Heartbeat:
        self.skews.append(clock_skew_seconds)
        if self._fail:
            raise RuntimeError("builder rusak")
        return _heartbeat()


def _sender(
    handler, builder: object | None = None, *, interval: float = 0.01
) -> tuple[HeartbeatSender, _Builder]:  # type: ignore[no-untyped-def]
    b = builder or _Builder()
    manager = NetworkManager([TransportSlot(adapter=_transport(handler))])
    return HeartbeatSender(manager=manager, builder=b, interval_seconds=interval), b  # type: ignore[arg-type]


def _central(heartbeats: list[httpx.Request], *, skew: float = 0.0, status: int = 200):  # type: ignore[no-untyped-def]
    def handler(r: httpx.Request) -> httpx.Response:
        if r.url.path == "/api/health":
            return httpx.Response(200, json={"ok": True})
        heartbeats.append(r)
        return httpx.Response(status, json=_response_body(skew_seconds=skew))

    return handler


class TestHeartbeatSender:
    async def test_beat_berhasil_mengirim_satu_heartbeat(self) -> None:
        received: list[httpx.Request] = []
        sender, _ = _sender(_central(received))

        assert await sender.beat_once() is True
        assert len(received) == 1

    async def test_central_tak_terjangkau_mengembalikan_false_tanpa_melempar(self) -> None:
        def down(_r: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("jaringan putus")

        sender, builder = _sender(down)

        assert await sender.beat_once() is False
        # Tidak perlu membangun heartbeat bila memang tak ada jalur keluar.
        assert builder.skews == []

    async def test_ditolak_central_tidak_menjatuhkan_agent(self) -> None:
        sender, _ = _sender(_central([], status=422))

        assert await sender.beat_once() is False

    async def test_central_5xx_tidak_menjatuhkan_agent(self) -> None:
        sender, _ = _sender(_central([], status=503))

        assert await sender.beat_once() is False

    async def test_builder_rusak_tidak_menjatuhkan_agent(self) -> None:
        sender, _ = _sender(_central([]), _Builder(fail=True))

        assert await sender.beat_once() is False

    async def test_selisih_jam_dihitung_dari_jawaban_dan_dikirim_di_beat_berikutnya(self) -> None:
        """Positif berarti jam kapal mendahului server."""
        sender, builder = _sender(_central([], skew=5.0))

        await sender.beat_once()
        await sender.beat_once()

        assert builder.skews[0] is None
        assert builder.skews[1] is not None
        assert 4.0 < builder.skews[1] < 6.5
        assert sender.clock_skew_seconds is not None
        assert 4.0 < sender.clock_skew_seconds < 6.5

    async def test_run_mengirim_berulang_sampai_dihentikan(self) -> None:
        received: list[httpx.Request] = []
        sender, _ = _sender(_central(received), interval=0.01)

        task = asyncio.create_task(sender.run())
        for _ in range(200):
            if len(received) >= 3:
                break
            await asyncio.sleep(0.01)
        sender.stop()
        await asyncio.wait_for(task, timeout=2.0)

        assert len(received) >= 3

    async def test_run_bertahan_walau_beberapa_beat_gagal(self) -> None:
        calls = {"n": 0}
        received: list[httpx.Request] = []

        def flaky(r: httpx.Request) -> httpx.Response:
            if r.url.path == "/api/health":
                return httpx.Response(200, json={"ok": True})
            calls["n"] += 1
            if calls["n"] <= 2:
                return httpx.Response(503)
            received.append(r)
            return httpx.Response(200, json=_response_body())

        sender, _ = _sender(flaky, interval=0.01)
        task = asyncio.create_task(sender.run())
        for _ in range(300):
            if received:
                break
            await asyncio.sleep(0.01)
        sender.stop()
        await asyncio.wait_for(task, timeout=2.0)

        assert received, "heartbeat tidak pulih setelah kegagalan berturut-turut"
