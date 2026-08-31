"""Gateway InfluxDB central.

Sama seperti di edge, memakai httpx langsung terhadap API HTTP InfluxDB. Enkoder
line protocol-nya pun sama bentuknya — tag identitas dan presisi mikrodetik —
supaya data dari kapal mendarat di central dengan skema yang identik.
"""

from __future__ import annotations

import httpx

from fleetview_common import StorageError, get_logger
from fleetview_contracts import Reading, encode_reading

__all__ = ["InfluxGateway"]

log = get_logger(__name__)


class InfluxWriteError(StorageError):
    code = "influx.write_failed"
    retryable = True


class InfluxGateway:
    """Menulis dan membaca telemetry di InfluxDB central."""

    def __init__(
        self,
        *,
        url: str,
        org: str,
        bucket: str,
        token: str,
        timeout_seconds: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._org, self._bucket = org, bucket
        self._client = client or httpx.AsyncClient(
            base_url=url.rstrip("/"),
            timeout=timeout_seconds,
            headers={"Authorization": f"Token {token}"} if token else {},
        )
        self._owns = client is None

    async def write_readings(self, readings: list[Reading], *, ship_id: str, device_id: str) -> int:
        """Tulis pembacaan dari satu batch. Kembalikan jumlah titik.

        `ship_id` dan `device_id` diambil dari envelope batch, bukan dari tiap
        pembacaan — itulah alasan keduanya tidak diulang ribuan kali di payload.
        """
        if not readings:
            return 0
        lines = [self._line(r, ship_id=ship_id, device_id=device_id) for r in readings]
        try:
            response = await self._client.post(
                "/api/v2/write",
                params={"org": self._org, "bucket": self._bucket, "precision": "us"},
                content="\n".join(lines).encode("utf-8"),
                headers={"Content-Type": "text/plain; charset=utf-8"},
            )
        except httpx.HTTPError as exc:
            raise InfluxWriteError(f"InfluxDB tidak terjangkau: {exc}") from exc

        if response.status_code != 204:
            # Pemetaan retryable di sini BERBEDA dari yang di edge, dan
            # perbedaannya penting.
            #
            # Di edge, 4xx dari InfluxDB lokal berarti datanya sendiri
            # bermasalah. Di central, sebagian besar 4xx justru berarti
            # **central yang salah konfigurasi** — token salah, bucket belum
            # dibuat, organisasi keliru. Melaporkannya sebagai penolakan
            # permanen akan membuat 70 kapal mengarantina data yang sebenarnya
            # baik-baik saja, dan data itu tidak akan pernah dicoba lagi.
            #
            # Hanya 400 dan 422 yang benar-benar menunjuk payload yang buruk.
            payload_is_bad = response.status_code in (400, 422)
            raise InfluxWriteError(
                f"InfluxDB menolak penulisan (HTTP {response.status_code}): {response.text[:300]}",
                details={
                    "influx_status": response.status_code,
                    "cause": "payload" if payload_is_bad else "konfigurasi central",
                },
                retryable=not payload_is_bad,
            )
        return len(lines)

    @staticmethod
    def _line(reading: Reading, *, ship_id: str, device_id: str) -> str:
        # Enkoder yang sama persis dengan yang dipakai edge — lihat
        # shared/contracts/influx_line.py.
        return encode_reading(reading, ship_id=ship_id, device_id=device_id)

    async def query_csv(self, flux: str) -> str:
        response = await self._client.post(
            "/api/v2/query",
            params={"org": self._org},
            content=flux.encode("utf-8"),
            headers={"Content-Type": "application/vnd.flux", "Accept": "application/csv"},
        )
        if response.status_code != 200:
            raise InfluxWriteError(
                f"query gagal (HTTP {response.status_code}): {response.text[:300]}"
            )
        return response.text

    async def healthy(self) -> bool:
        try:
            r = await self._client.get("/health", timeout=5.0)
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def close(self) -> None:
        if self._owns:
            await self._client.aclose()
