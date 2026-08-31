"""Penyimpanan telemetry InfluxDB v2 lewat HTTP langsung.

Memakai `httpx` terhadap API HTTP InfluxDB, bukan `influxdb-client`. Alasannya:

- Yang kita butuhkan hanya dua endpoint: tulis dan health. Client resmi menarik
  reactivex dan beberapa dependency lain untuk fungsi yang tidak dipakai — pada
  Raspberry Pi 4 GB yang juga menjalankan InfluxDB itu sendiri, itu bukan hal
  sepele.
- Kita sudah mengenkode line protocol sendiri (`lineprotocol.py`), sehingga
  bagian yang paling rawan salah bisa diuji tanpa database.
- Percobaan ulang dan buffering memang kita tangani sendiri
  (`buffer.py`, `writer.py`), jadi fitur retry bawaan client tidak terpakai.
- `httpx` tetap dibutuhkan Sync Engine di Phase 4.

Pemetaan status HTTP ke taksonomi error kita sengaja mengikuti aturan retry yang
sama dengan protokol sync: 4xx berarti jangan diulang, 5xx dan 429 berarti boleh.
"""

from __future__ import annotations

import httpx

from fleetview_common import get_logger, now_micros
from fleetview_contracts import TelemetryRecord, encode_records
from fleetview_edge.storage.base import (
    StorageHealth,
    StorageRejectedError,
    StorageRetentionRejectedError,
    StorageState,
    StorageUnavailableError,
    TelemetryStore,
)
from fleetview_edge.storage.retention import RetentionPolicy

__all__ = ["InfluxTelemetryStore"]

log = get_logger(__name__)


class InfluxTelemetryStore(TelemetryStore):
    """Menulis telemetry ke InfluxDB 2.x lokal.

    Args:
        url: alamat dasar InfluxDB, mis. `http://127.0.0.1:8086`.
        org: organisasi.
        bucket: bucket tujuan.
        token: token API. Kosong hanya boleh saat pengembangan.
        retention: kebijakan retensi yang diterapkan saat `ensure_ready()`.
        timeout_seconds: batas waktu satu permintaan HTTP.
    """

    name = "influxdb"

    def __init__(
        self,
        *,
        url: str,
        org: str,
        bucket: str,
        token: str = "",
        retention: RetentionPolicy | None = None,
        timeout_seconds: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = url.rstrip("/")
        self._org = org
        self._bucket = bucket
        self._token = token
        self._retention = retention or RetentionPolicy()
        self._client = client or httpx.AsyncClient(
            base_url=self._url,
            timeout=timeout_seconds,
            headers=self._auth_headers(),
        )
        self._owns_client = client is None
        self._last_success_us: int | None = None
        self._consecutive_failures = 0
        self._counters: dict[str, int] = {"writes": 0, "records": 0, "failures": 0}

    @property
    def retention(self) -> RetentionPolicy:
        """Kebijakan retensi yang diterapkan store ini.

        Publik karena Health Monitor dan Edge Console perlu menampilkannya —
        "data disimpan berapa lama di kapal ini" adalah pertanyaan operasional
        yang sering ditanyakan, bukan detail internal.
        """
        return self._retention

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Token {self._token}"} if self._token else {}

    # -- penulisan ----------------------------------------------------------

    async def write(self, records: list[TelemetryRecord]) -> None:
        if not records:
            return

        payload = encode_records(records)

        try:
            response = await self._client.post(
                "/api/v2/write",
                params={"org": self._org, "bucket": self._bucket, "precision": "us"},
                content=payload.encode("utf-8"),
                headers={"Content-Type": "text/plain; charset=utf-8"},
            )
        except httpx.HTTPError as exc:
            self._consecutive_failures += 1
            self._counters["failures"] += 1
            raise StorageUnavailableError(
                f"InfluxDB tidak bisa dijangkau: {exc}",
                details={"url": self._url, "records": len(records)},
            ) from exc

        self._raise_for_status(response, len(records))

        self._last_success_us = now_micros()
        self._consecutive_failures = 0
        self._counters["writes"] += 1
        self._counters["records"] += len(records)

    def _raise_for_status(self, response: httpx.Response, record_count: int) -> None:
        if response.status_code == 204:
            return

        self._consecutive_failures += 1
        self._counters["failures"] += 1
        body = response.text[:500]
        details = {
            "status": response.status_code,
            "body": body,
            "records": record_count,
        }

        # 4xx (kecuali 429) berarti datanya sendiri yang bermasalah — payload
        # salah bentuk, token ditolak, bucket tidak ada. Mengulang tidak akan
        # menolong, dan kalau kita tetap mengulang, satu batch rusak akan
        # menyumbat buffer sementara data baru terbuang di belakangnya.
        if response.status_code == 429 or response.status_code >= 500:
            retry_after = response.headers.get("Retry-After")
            raise StorageUnavailableError(
                f"InfluxDB menolak sementara (HTTP {response.status_code})",
                details={**details, "retry_after": retry_after},
            )

        # InfluxDB memakai 422 juga untuk titik yang jatuh di luar retensi
        # bucket. Bedakan, karena penyebab dan tindakannya sama sekali berbeda:
        # payload salah bentuk adalah bug, sedangkan data di luar retensi berarti
        # kapal offline lebih lama dari umur simpan lokalnya.
        if "retention policy" in body.lower():
            raise StorageRetentionRejectedError(
                f"InfluxDB membuang titik di luar retensi bucket: {body}",
                details={
                    **details,
                    "hint": (
                        "retention_days harus setidaknya sepanjang durasi offline "
                        "terburuk yang diperkirakan"
                    ),
                },
            )

        raise StorageRejectedError(
            f"InfluxDB menolak penulisan (HTTP {response.status_code}): {body}",
            details=details,
        )

    # -- penyiapan ----------------------------------------------------------

    async def ensure_ready(self) -> None:
        """Pastikan bucket ada dengan retensi yang benar.

        Kegagalan di sini **tidak menghentikan agent**: kapal tetap harus
        mengumpulkan data meski penyiapan database gagal, dan bucket mungkin
        saja sudah disiapkan lewat provisioning.
        """
        try:
            found = await self._client.get(
                "/api/v2/buckets", params={"name": self._bucket, "org": self._org}
            )
            if found.status_code == 200 and found.json().get("buckets"):
                log.info(
                    "storage.bucket_exists",
                    bucket=self._bucket,
                    retention_days=self._retention.days,
                )
                return

            created = await self._client.post(
                "/api/v2/buckets",
                json={
                    "name": self._bucket,
                    "orgID": await self._org_id(),
                    "retentionRules": self._retention.to_bucket_rules(),
                },
            )
            if created.status_code in (200, 201):
                log.info(
                    "storage.bucket_created",
                    bucket=self._bucket,
                    retention_days=self._retention.days,
                )
            else:
                log.warning(
                    "storage.bucket_setup_failed",
                    bucket=self._bucket,
                    status=created.status_code,
                    body=created.text[:200],
                )
        except httpx.HTTPError as exc:
            log.warning(
                "storage.bucket_setup_unreachable",
                bucket=self._bucket,
                error=str(exc),
                note="akuisisi tetap berjalan; penyiapan bucket dicoba lagi nanti",
            )

    async def _org_id(self) -> str | None:
        response = await self._client.get("/api/v2/orgs", params={"org": self._org})
        if response.status_code != 200:
            return None
        orgs = response.json().get("orgs") or []
        return str(orgs[0]["id"]) if orgs else None

    # -- kesehatan ----------------------------------------------------------

    async def health(self) -> StorageHealth:
        reachable = False
        detail: str | None = None
        try:
            response = await self._client.get("/health", timeout=3.0)
            reachable = response.status_code == 200
            if not reachable:
                detail = f"/health mengembalikan HTTP {response.status_code}"
        except httpx.HTTPError as exc:
            detail = f"tidak bisa dijangkau: {exc}"

        if not reachable:
            state = StorageState.UNAVAILABLE
        elif self._consecutive_failures:
            state = StorageState.DEGRADED
        else:
            state = StorageState.HEALTHY

        return StorageHealth(
            state=state,
            reachable=reachable,
            last_success_us=self._last_success_us,
            consecutive_failures=self._consecutive_failures,
            detail=detail,
            counters=dict(self._counters),
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
