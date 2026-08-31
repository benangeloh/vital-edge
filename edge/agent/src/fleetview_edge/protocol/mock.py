"""MockLPAAdapter — adapter deterministik yang bisa diskenariokan.

Dipakai unit test dan integration test. Berbeda dari SimulatorAdapter yang
meniru mesin kapal sungguhan, adapter ini tidak berusaha realistis: ia berusaha
**bisa diprediksi**, sehingga sebuah test bisa menyatakan "pada putaran ke-3,
sambungan putus" dan hasilnya sama setiap kali dijalankan.

Kegagalan yang bisa diskenariokan itu penting: menangani putusnya sambungan,
timeout, dan nilai rusak adalah persyaratan Phase 2, dan hal-hal itu mustahil
diuji dengan andal kalau hanya menunggu kebetulan.
"""

from __future__ import annotations

from collections.abc import Sequence

from fleetview_common import now_micros
from fleetview_edge.protocol.base import (
    AdapterHealth,
    LinkState,
    ProtocolAdapter,
    ProtocolNotConnectedError,
    ProtocolTimeoutError,
    RawPoint,
)

__all__ = ["MockLPAAdapter", "MockScript"]


class MockScript:
    """Skenario perilaku per putaran pembacaan.

    Putaran dihitung mulai 0. Sebuah nomor putaran yang muncul di beberapa
    himpunan akan dievaluasi berurutan: timeout lebih dulu, lalu putus sambungan,
    lalu kegagalan sebagian.
    """

    def __init__(
        self,
        *,
        timeout_on: Sequence[int] = (),
        disconnect_on: Sequence[int] = (),
        fail_channels_on: dict[int, Sequence[str]] | None = None,
        connect_failures: int = 0,
    ) -> None:
        self.timeout_on = set(timeout_on)
        self.disconnect_on = set(disconnect_on)
        self.fail_channels_on = {k: set(v) for k, v in (fail_channels_on or {}).items()}
        self.connect_failures = connect_failures
        """Berapa kali connect() gagal sebelum akhirnya berhasil.
        Untuk menguji backoff dan reconnect."""


class MockLPAAdapter(ProtocolAdapter):
    """Adapter yang mengembalikan nilai tetap, dengan kegagalan yang bisa diatur.

    Args:
        values: channel -> nilai. Dikembalikan apa adanya setiap putaran.
        script: skenario kegagalan opsional.
    """

    name = "mock"

    def __init__(
        self,
        values: dict[str, float | int | bool | str] | None = None,
        *,
        script: MockScript | None = None,
    ) -> None:
        self._values = dict(values or {})
        self._script = script or MockScript()
        self._connected = False
        self._round = 0
        self._connect_attempts = 0
        self._connected_since_us: int | None = None
        self._last_success_us: int | None = None
        self._consecutive_failures = 0
        self._counters: dict[str, int] = {"reads": 0, "failures": 0, "connects": 0}

    # -- kendali untuk test -------------------------------------------------

    @property
    def round_number(self) -> int:
        """Berapa kali read_points() sudah dipanggil."""
        return self._round

    @property
    def connect_attempts(self) -> int:
        return self._connect_attempts

    @property
    def is_connected(self) -> bool:
        return self._connected

    def set_value(self, channel: str, value: float | int | bool | str) -> None:
        """Ubah nilai satu channel di tengah jalan — untuk menguji laju perubahan."""
        self._values[channel] = value

    def drop_channel(self, channel: str) -> None:
        """Hilangkan sebuah channel — untuk menguji deteksi staleness."""
        self._values.pop(channel, None)

    def force_disconnect(self) -> None:
        self._connected = False
        self._connected_since_us = None

    # -- ProtocolAdapter ----------------------------------------------------

    async def connect(self) -> None:
        self._connect_attempts += 1
        self._counters["connects"] += 1
        if self._connect_attempts <= self._script.connect_failures:
            self._consecutive_failures += 1
            raise ProtocolNotConnectedError(
                f"mock: connect gagal (percobaan ke-{self._connect_attempts})",
                details={"attempt": self._connect_attempts},
            )
        self._connected = True
        self._connected_since_us = now_micros()
        self._consecutive_failures = 0

    async def read_points(self) -> list[RawPoint]:
        if not self._connected:
            raise ProtocolNotConnectedError("mock: belum tersambung")

        current = self._round
        self._round += 1

        if current in self._script.timeout_on:
            self._consecutive_failures += 1
            self._counters["failures"] += 1
            raise ProtocolTimeoutError(
                f"mock: timeout pada putaran {current}", details={"round": current}
            )

        if current in self._script.disconnect_on:
            self._connected = False
            self._connected_since_us = None
            self._consecutive_failures += 1
            self._counters["failures"] += 1
            raise ProtocolNotConnectedError(
                f"mock: sambungan putus pada putaran {current}", details={"round": current}
            )

        # Kegagalan sebagian: channel yang gagal dihilangkan, sisanya tetap
        # dikembalikan. Satu sensor rusak tidak boleh membuat 39 sensor lain buta.
        failed = self._script.fail_channels_on.get(current, set())
        now = now_micros()
        points = [
            RawPoint(channel=ch, value=val, read_at_us=now)
            for ch, val in self._values.items()
            if ch not in failed
        ]

        self._last_success_us = now
        self._consecutive_failures = 0
        self._counters["reads"] += 1
        return points

    async def health(self) -> AdapterHealth:
        if not self._connected:
            link = LinkState.DISCONNECTED
        elif self._consecutive_failures:
            link = LinkState.DEGRADED
        else:
            link = LinkState.CONNECTED
        return AdapterHealth(
            link=link,
            connected_since_us=self._connected_since_us,
            last_success_us=self._last_success_us,
            consecutive_failures=self._consecutive_failures,
            counters=dict(self._counters),
        )

    async def close(self) -> None:
        self._connected = False
        self._connected_since_us = None
