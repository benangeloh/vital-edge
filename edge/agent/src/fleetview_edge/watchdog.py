"""Integrasi watchdog systemd, memakai protokol sd_notify.

Kenapa ini ada: Raspberry Pi di kapal tidak punya orang yang mengawasinya.
`Restart=always` hanya menolong kalau proses benar-benar mati. Kegagalan yang
lebih berbahaya adalah proses yang **masih hidup tetapi berhenti bekerja** —
loop akuisisi tersangkut pada pembacaan serial yang tidak pernah kembali,
misalnya. Bagi systemd proses itu tampak sehat, dan kapal berhenti mengumpulkan
data tanpa ada yang tahu sampai berminggu-minggu kemudian.

Watchdog membalik tanggung jawabnya: agent harus **membuktikan** dirinya masih
bekerja secara berkala. Kalau bukti itu berhenti datang, systemd membunuh dan
menjalankannya ulang.

Protokolnya ditulis langsung, bukan lewat pustaka. sd_notify hanyalah datagram
teks ke sebuah unix socket; menambahkan dependency `systemd-python` — yang butuh
kompilasi dan header libsystemd — untuk selusin baris ini tidak sepadan pada
perangkat yang harus tetap bisa di-provisioning tanpa jaringan.

Di luar systemd (pengembangan di macOS, kontainer, jalan manual) `NOTIFY_SOCKET`
tidak ada dan semua fungsi di sini menjadi tanpa efek.
"""

from __future__ import annotations

import os
import socket
from typing import Final

from fleetview_common import get_logger

__all__ = ["SystemdNotifier"]

log = get_logger(__name__)

_ABSTRACT_PREFIX: Final = "@"


class SystemdNotifier:
    """Pengirim pesan sd_notify.

    Dibuat sekali saat proses mulai. Bila `NOTIFY_SOCKET` tidak diset, objeknya
    tetap bisa dipakai tetapi tidak mengirim apa pun — pemanggil tidak perlu
    membedakan berjalan di bawah systemd atau tidak.
    """

    def __init__(self, address: str | None = None) -> None:
        self._address = address if address is not None else os.environ.get("NOTIFY_SOCKET")
        self._socket: socket.socket | None = None
        if self._address:
            try:
                self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            except OSError:
                # AF_UNIX tidak tersedia (Windows) atau socket habis. Bukan
                # alasan untuk menghentikan agent — data jauh lebih penting
                # daripada pelaporan kesehatan.
                log.warning("watchdog.socket_gagal", note="watchdog dinonaktifkan")
                self._socket = None

    @property
    def enabled(self) -> bool:
        return self._socket is not None

    @property
    def interval_seconds(self) -> float | None:
        """Selang ping yang aman, yaitu setengah dari `WatchdogSec`.

        systemd memberi `WATCHDOG_USEC` sebagai batas keras. Melapor tepat di
        batas itu akan memicu restart palsu pada jitter penjadwalan sekecil apa
        pun — dan restart palsu pada kapal berarti kehilangan siklus akuisisi
        tanpa sebab. Setengahnya adalah anjuran systemd sendiri.
        """
        raw = os.environ.get("WATCHDOG_USEC")
        if not raw or not self.enabled:
            return None
        try:
            usec = int(raw)
        except ValueError:
            return None
        return usec / 2_000_000 if usec > 0 else None

    def _send(self, message: str) -> None:
        if self._socket is None or not self._address:
            return
        # Alamat berawalan '@' berarti abstract namespace socket, yang di API
        # soket Python diwakili byte NUL di awal.
        address = self._address
        path = "\0" + address[1:] if address.startswith(_ABSTRACT_PREFIX) else address
        try:
            self._socket.sendto(message.encode("utf-8"), path)
        except OSError:
            # Notifikasi yang gagal tidak boleh menjatuhkan agent. Kalau systemd
            # berhenti mendengar, watchdog-nya sendiri yang akan bertindak.
            log.debug("watchdog.kirim_gagal", message=message)

    def ready(self) -> None:
        """Umumkan bahwa akuisisi sudah berjalan.

        Dipanggil setelah komponen hidup, bukan saat proses baru mulai: dengan
        `Type=notify`, systemd menganggap unit belum aktif sampai pesan ini
        datang, sehingga unit yang bergantung padanya tidak start terlalu dini.
        """
        self._send("READY=1")

    def alive(self) -> None:
        """Bukti berkala bahwa agent masih benar-benar bekerja."""
        self._send("WATCHDOG=1")

    def stopping(self) -> None:
        """Umumkan penghentian yang disengaja, supaya tidak dianggap crash."""
        self._send("STOPPING=1")

    def status(self, text: str) -> None:
        """Satu baris status yang muncul di `systemctl status`.

        Ini yang dilihat teknisi lebih dulu saat naik ke kapal, jadi isinya
        harus angka operasional, bukan sekadar 'running'.
        """
        self._send(f"STATUS={text}")

    def close(self) -> None:
        if self._socket is not None:
            self._socket.close()
            self._socket = None
