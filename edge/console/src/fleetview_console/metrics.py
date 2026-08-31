"""Pembacaan metrik sistem.

Target utamanya Raspberry Pi yang menjalankan Linux, jadi sumbernya dibaca
langsung dari `/proc` dan `/sys` alih-alih memakai psutil. Alasannya bukan
ideologi: agent ini berjalan 24/7 di perangkat 4 GB yang juga menjalankan
InfluxDB, dan setiap dependency yang bisa dihindari adalah satu hal lagi yang
tidak perlu di-update, di-audit, dan dimuat ke memori.

Di platform lain (mis. macOS saat pengembangan) pembacaannya mengembalikan None,
bukan menebak. Angka yang dikarang di panel kesehatan lebih buruk daripada
tanda strip.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

__all__ = ["SystemMetrics"]

_PROC_STAT = Path("/proc/stat")
_PROC_MEMINFO = Path("/proc/meminfo")
_PROC_UPTIME = Path("/proc/uptime")

#: Lokasi sensor suhu yang lazim di Raspberry Pi dan SBC lain.
_THERMAL_PATHS = (
    Path("/sys/class/thermal/thermal_zone0/temp"),
    Path("/sys/devices/virtual/thermal/thermal_zone0/temp"),
)


class SystemMetrics:
    """Membaca CPU, memori, suhu, dan disk.

    CPU dihitung sebagai selisih antara dua pembacaan `/proc/stat`, sehingga
    instance ini harus berumur panjang — membuat instance baru tiap permintaan
    akan selalu menghasilkan None pada pembacaan pertama.
    """

    def __init__(self, *, data_dir: Path | str = "/") -> None:
        self._data_dir = Path(data_dir)
        self._last_cpu: tuple[int, int] | None = None
        self._started = time.monotonic()

    # -- CPU ----------------------------------------------------------------

    def cpu_percent(self) -> float | None:
        sample = self._read_cpu_sample()
        if sample is None:
            return None

        previous, self._last_cpu = self._last_cpu, sample
        if previous is None:
            # Pembacaan pertama tidak punya pembanding. Mengembalikan 0 akan
            # terlihat seperti CPU menganggur, padahal kita hanya belum tahu.
            return None

        idle_delta = sample[0] - previous[0]
        total_delta = sample[1] - previous[1]
        if total_delta <= 0:
            return None
        return round(100.0 * (1.0 - idle_delta / total_delta), 1)

    @staticmethod
    def _read_cpu_sample() -> tuple[int, int] | None:
        try:
            first = _PROC_STAT.read_text(encoding="utf-8").splitlines()[0]
        except (OSError, IndexError):
            return None
        parts = first.split()
        if len(parts) < 5 or parts[0] != "cpu":
            return None
        try:
            values = [int(v) for v in parts[1:]]
        except ValueError:
            return None
        # idle + iowait dianggap menganggur.
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        return idle, sum(values)

    # -- Memori -------------------------------------------------------------

    def memory_percent(self) -> float | None:
        """Persen memori terpakai, memakai MemAvailable.

        MemAvailable, bukan MemFree: Linux memakai memori bebas untuk cache, dan
        MemFree akan selalu terlihat mengkhawatirkan padahal tidak.
        """
        info = self._read_meminfo()
        total, available = info.get("MemTotal"), info.get("MemAvailable")
        if not total or available is None:
            return None
        return round(100.0 * (1.0 - available / total), 1)

    @staticmethod
    def _read_meminfo() -> dict[str, int]:
        try:
            raw = _PROC_MEMINFO.read_text(encoding="utf-8")
        except OSError:
            return {}
        out: dict[str, int] = {}
        for line in raw.splitlines():
            key, _, rest = line.partition(":")
            value = rest.strip().split(" ")[0]
            if value.isdigit():
                out[key] = int(value)
        return out

    # -- Suhu ---------------------------------------------------------------

    def temperature_celsius(self) -> float | None:
        """Suhu CPU. Penting di kapal: ruang mesin panas, dan Pi yang throttle
        akan melewatkan siklus polling tanpa memberi tanda lain."""
        for path in _THERMAL_PATHS:
            try:
                raw = path.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            try:
                millidegrees = int(raw)
            except ValueError:
                continue
            return round(millidegrees / 1000.0, 1)
        return None

    # -- Disk ---------------------------------------------------------------

    def disk(self) -> tuple[float | None, int | None]:
        """(persen terpakai, byte bebas) pada partisi data.

        Kalau direktori data belum ada — kasus nyata pada Pi yang baru
        di-provision — pemeriksaan naik ke induk terdekat yang ada. Filesystem
        yang akan menampung data itu tetap informasi yang berguna; menampilkan
        strip hanya karena direktorinya belum dibuat justru menyembunyikan disk
        yang mungkin sudah hampir penuh.
        """
        try:
            target = self._data_dir
            for candidate in (target, *target.parents):
                if candidate.exists():
                    target = candidate
                    break
            else:
                return None, None
            usage = shutil.disk_usage(target)
        except (OSError, ValueError):
            # ValueError ikut ditangkap: path yang mengandung byte nol membuat
            # exists() melempar, bukan mengembalikan False. Panel kesehatan tidak
            # boleh gagal total karena satu path yang salah bentuk di config.
            return None, None
        if usage.total <= 0:
            return None, None
        return round(100.0 * usage.used / usage.total, 1), usage.free

    def uptime_seconds(self) -> int | None:
        """Uptime sistem, bukan uptime proses.

        Teknisi yang bertanya "apakah Pi ini baru restart" sedang menanyakan
        perangkatnya, bukan prosesnya.
        """
        try:
            raw = _PROC_UPTIME.read_text(encoding="utf-8").split()[0]
            return int(float(raw))
        except (OSError, IndexError, ValueError):
            return None

    def process_uptime_seconds(self) -> int:
        return int(time.monotonic() - self._started)
