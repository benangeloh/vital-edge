"""Penyimpanan telemetry in-memory, untuk pengujian dan pengembangan.

Memenuhi kontrak `TelemetryStore` yang sama dengan InfluxDB, sehingga seluruh
jalur — writer, buffer, penanganan kegagalan — bisa diuji tanpa database yang
berjalan. Kegagalannya bisa diskenariokan, dengan alasan yang sama seperti pada
MockLPAAdapter: penanganan kegagalan mustahil diuji dengan andal kalau hanya
menunggu kebetulan.
"""

from __future__ import annotations

from collections.abc import Sequence

from fleetview_common import now_micros
from fleetview_contracts import TelemetryRecord
from fleetview_edge.storage.base import (
    StorageHealth,
    StorageRejectedError,
    StorageState,
    StorageUnavailableError,
    TelemetryStore,
)

__all__ = ["InMemoryTelemetryStore"]


class InMemoryTelemetryStore(TelemetryStore):
    """Menyimpan record di memori.

    Args:
        fail_writes_on: nomor penulisan (mulai 0) yang harus gagal sementara.
        reject_writes_on: nomor penulisan yang harus ditolak permanen.
        unavailable: bila True, setiap penulisan gagal sampai `recover()`.
    """

    name = "memory"

    def __init__(
        self,
        *,
        fail_writes_on: Sequence[int] = (),
        reject_writes_on: Sequence[int] = (),
        unavailable: bool = False,
    ) -> None:
        self.records: list[TelemetryRecord] = []
        self._fail_on = set(fail_writes_on)
        self._reject_on = set(reject_writes_on)
        self._unavailable = unavailable
        self._write_count = 0
        self._last_success_us: int | None = None
        self._consecutive_failures = 0
        self.ensure_ready_calls = 0
        self.closed = False

    # -- kendali untuk test -------------------------------------------------

    @property
    def write_attempts(self) -> int:
        return self._write_count

    def go_down(self) -> None:
        self._unavailable = True

    def recover(self) -> None:
        self._unavailable = False
        self._consecutive_failures = 0

    # -- TelemetryStore -----------------------------------------------------

    async def write(self, records: list[TelemetryRecord]) -> None:
        current = self._write_count
        self._write_count += 1

        if current in self._reject_on:
            self._consecutive_failures += 1
            raise StorageRejectedError(
                f"memory store menolak penulisan {current}", details={"write": current}
            )

        if self._unavailable or current in self._fail_on:
            self._consecutive_failures += 1
            raise StorageUnavailableError(
                f"memory store tidak tersedia pada penulisan {current}",
                details={"write": current},
            )

        self.records.extend(records)
        self._last_success_us = now_micros()
        self._consecutive_failures = 0

    async def health(self) -> StorageHealth:
        if self._unavailable:
            state = StorageState.UNAVAILABLE
        elif self._consecutive_failures:
            state = StorageState.DEGRADED
        else:
            state = StorageState.HEALTHY
        return StorageHealth(
            state=state,
            reachable=not self._unavailable,
            last_success_us=self._last_success_us,
            consecutive_failures=self._consecutive_failures,
            counters={"records": len(self.records), "writes": self._write_count},
        )

    async def ensure_ready(self) -> None:
        self.ensure_ready_calls += 1

    async def close(self) -> None:
        self.closed = True
