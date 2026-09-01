"""Test integrasi watchdog systemd.

Yang diuji di sini bukan sekadar "pesan terkirim", melainkan sifat yang membuat
watchdog ada gunanya: agent hanya melapor hidup ketika akuisisi benar-benar maju.
"""

from __future__ import annotations

import socket
import tempfile
from pathlib import Path

import pytest

from fleetview_edge.watchdog import SystemdNotifier


@pytest.fixture
def listener() -> tuple[socket.socket, str]:
    tmp = tempfile.mkdtemp()
    path = str(Path(tmp) / "notify.sock")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    sock.bind(path)
    sock.settimeout(1.0)
    return sock, path


class TestTanpaSystemd:
    def test_nonaktif_saat_notify_socket_tidak_ada(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Di macOS, kontainer, atau saat dijalankan manual, semua panggilan
        harus menjadi tanpa efek — bukan error."""
        monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
        notifier = SystemdNotifier()
        assert not notifier.enabled
        notifier.ready()
        notifier.alive()
        notifier.status("apa pun")
        notifier.stopping()
        notifier.close()

    def test_interval_none_saat_nonaktif(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
        monkeypatch.setenv("WATCHDOG_USEC", "30000000")
        assert SystemdNotifier().interval_seconds is None


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="butuh AF_UNIX")
class TestProtokol:
    def test_pesan_sampai_ke_socket(self, listener: tuple[socket.socket, str]) -> None:
        sock, path = listener
        notifier = SystemdNotifier(address=path)
        assert notifier.enabled

        notifier.ready()
        assert sock.recv(64) == b"READY=1"
        notifier.alive()
        assert sock.recv(64) == b"WATCHDOG=1"
        notifier.stopping()
        assert sock.recv(64) == b"STOPPING=1"
        notifier.close()

    def test_status_terkirim_apa_adanya(self, listener: tuple[socket.socket, str]) -> None:
        """Baris ini yang dilihat teknisi lebih dulu di `systemctl status`."""
        sock, path = listener
        notifier = SystemdNotifier(address=path)
        notifier.status("polls=42 pending=0 record")
        assert sock.recv(128) == b"STATUS=polls=42 pending=0 record"
        notifier.close()

    def test_socket_hilang_tidak_menjatuhkan_agent(
        self, listener: tuple[socket.socket, str]
    ) -> None:
        """Pelaporan kesehatan tidak pernah lebih penting daripada data."""
        sock, path = listener
        notifier = SystemdNotifier(address=path)
        sock.close()
        Path(path).unlink()
        notifier.alive()  # tidak boleh melempar
        notifier.close()


class TestInterval:
    def test_setengah_dari_watchdog_usec(
        self, listener: tuple[socket.socket, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Melapor tepat di batas keras akan memicu restart palsu pada jitter
        penjadwalan sekecil apa pun — dan restart palsu di kapal berarti
        kehilangan siklus akuisisi tanpa sebab."""
        _, path = listener
        monkeypatch.setenv("WATCHDOG_USEC", "30000000")  # 30 detik
        assert SystemdNotifier(address=path).interval_seconds == 15.0

    @pytest.mark.parametrize("raw", ["", "bukan-angka", "0"])
    def test_nilai_tidak_masuk_akal_menonaktifkan_heartbeat(
        self, listener: tuple[socket.socket, str], monkeypatch: pytest.MonkeyPatch, raw: str
    ) -> None:
        _, path = listener
        monkeypatch.setenv("WATCHDOG_USEC", raw)
        assert SystemdNotifier(address=path).interval_seconds is None


class TestHeartbeatTerikatAkuisisi:
    """Sifat yang membuat watchdog ada gunanya.

    Ping tanpa syarat hanya membuktikan event loop berputar — dan itu justru
    bukan kegagalan yang perlu ditangkap. Proses yang loop akuisisinya tersangkut
    pada pembacaan serial yang tidak pernah kembali akan tetap mem-ping dengan
    riang sementara kapal berhenti mengumpulkan data.
    """

    @staticmethod
    async def _jalankan(polls: list[int], *, awal: int = 0, interval: float = 0.01) -> list[bytes]:
        import asyncio
        from dataclasses import dataclass

        from fleetview_edge.runtime import EdgeRuntime

        @dataclass
        class _Stats:
            polls_attempted: int = 0

        class _Collector:
            def __init__(self) -> None:
                self.stats = _Stats(polls_attempted=awal)

        sent: list[bytes] = []

        class _Notifier:
            enabled = True

            def alive(self) -> None:
                sent.append(b"WATCHDOG=1")

            def status(self, text: str) -> None: ...

        runtime = EdgeRuntime.__new__(EdgeRuntime)
        runtime._stopping = asyncio.Event()
        runtime.collector = _Collector()  # type: ignore[assignment]
        runtime.outbox = None

        async def _drive() -> None:
            for value in polls:
                runtime.collector.stats.polls_attempted = value  # type: ignore[union-attr]
                await asyncio.sleep(interval * 1.5)
            runtime._stopping.set()

        await asyncio.gather(
            runtime._heartbeat(_Notifier(), interval),  # type: ignore[arg-type]
            _drive(),
        )
        return sent

    @pytest.mark.asyncio
    async def test_ping_saat_akuisisi_maju(self) -> None:
        sent = await self._jalankan([1, 2, 3], awal=0)
        assert len(sent) >= 3

    @pytest.mark.asyncio
    async def test_berhenti_ping_saat_akuisisi_macet(self) -> None:
        """Jumlah poll yang tidak bertambah berarti agent hidup tetapi tidak
        bekerja. Di situlah systemd harus mengambil alih."""
        sent = await self._jalankan([7, 7, 7, 7, 7], awal=7)
        assert len(sent) == 1, (
            "hanya kelonggaran start-up; setelah itu akuisisi tidak maju dan ping berhenti"
        )
