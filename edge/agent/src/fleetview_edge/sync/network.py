"""NetworkTransport — pengiriman HTTP yang bisa dilanjutkan.

Dipakai untuk LAN, Wi-Fi, dan seluler. Ketiganya hanyalah HTTP lewat rute yang
berbeda; yang membedakan hanya kualitas link dan anggaran kuota, dan itu urusan
NetworkManager — bukan urusan transport ini.

Protokolnya mengikuti docs/architecture/05-sync-protocol.md:

    POST /ingest/sessions          buka atau lanjutkan
    PUT  /sessions/{id}/chunks/{i} kirim potongan
    POST /sessions/{id}/commit     verifikasi checksum -> ACK

Kemampuan melanjutkan itu bukan kemewahan. Satelit kapal putus di tengah
transfer adalah kejadian biasa, dan tanpa resume, backlog 30 hari tidak akan
pernah selesai terkirim — setiap percobaan mulai dari nol lagi dan putus di
tempat yang kira-kira sama.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from fleetview_common import get_logger
from fleetview_contracts import Ack, BatchEnvelope
from fleetview_edge.sync.base import (
    TransportAdapter,
    TransportHealth,
    TransportKind,
    TransportRejectedError,
    TransportResult,
    TransportUnavailableError,
)

__all__ = ["NetworkTransport"]

log = get_logger(__name__)

DEFAULT_SINGLE_SHOT_LIMIT = 1_048_576
"""Di bawah ukuran ini, kirim sekali jalan.

Membuka sesi berpotongan untuk payload 20 KB hanya menambah tiga round trip
tanpa manfaat. Ambangnya bisa diturunkan per-link: pada satelit yang lambat dan
sering putus, sekali jalan sebesar 1 MB berarti setiap kegagalan membuang
seluruh progres — potongan kecil membuat kemajuan bisa dipertahankan.
"""


class NetworkTransport(TransportAdapter):
    """Mengirim batch ke central lewat HTTPS.

    Autentikasi punya dua bentuk, dan yang kedua adalah yang dipakai di kapal:

    - `token` — JWT statis. Praktis untuk pengujian, tetapi **kedaluwarsa**
      (default satu jam di central). Kapal yang hanya dibekali ini akan berhenti
      menyetor data satu jam setelah dinyalakan, dan tidak akan pulih sampai ada
      orang yang mengganti berkas konfigurasinya.
    - `client_id` + `secret` — kredensial jangka panjang yang ditukar menjadi
      token saat dibutuhkan, dan ditukar ulang secara otomatis ketika token
      kedaluwarsa. **Ini yang dipakai di produksi.**

    Bentuk kedua juga yang membuat pencabutan kredensial berarti: begitu central
    mencabutnya, penukaran berikutnya gagal dan kapal berhenti bisa mengirim.
    Dengan token statis, pencabutan tidak berpengaruh sampai token itu sendiri
    kedaluwarsa.

    Args:
        base_url: alamat central.
        token: JWT device statis. Untuk pengujian; lihat catatan di atas.
        client_id, secret: kredensial device jangka panjang.
        kind: jenis link yang diwakili (untuk pelaporan status).
        chunk_size: ukuran potongan saat memakai sesi berpotongan.
        single_shot_limit: di bawah ukuran ini, kirim sekali jalan tanpa sesi.
    """

    def __init__(
        self,
        *,
        base_url: str,
        token: str = "",
        client_id: str = "",
        secret: str = "",
        kind: TransportKind = TransportKind.LAN,
        chunk_size: int = 262_144,
        single_shot_limit: int = DEFAULT_SINGLE_SHOT_LIMIT,
        timeout_seconds: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.kind = kind
        self._base = base_url.rstrip("/")
        self._chunk_size = chunk_size
        self._single_shot_limit = single_shot_limit
        self._client_id = client_id
        self._secret = secret
        self._token = token
        self._client = client or httpx.AsyncClient(base_url=self._base, timeout=timeout_seconds)
        self._owns_client = client is None
        self._counters = {"sent": 0, "resumed": 0, "bytes": 0, "token_refresh": 0}
        # Satu penukaran pada satu waktu. Tanpa ini, beberapa permintaan yang
        # bersamaan menemui 401 akan menukar kredensial serentak — memenuhi
        # rate limit `auth` di central dengan permintaan yang saling menduplikasi.
        self._auth_lock = asyncio.Lock()

    @property
    def _can_refresh(self) -> bool:
        return bool(self._client_id and self._secret)

    def _auth_header(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    async def _obtain_token(self) -> None:
        """Tukar kredensial jangka panjang dengan token baru.

        Kegagalan di sini dilaporkan sebagai `TransportUnavailableError`, bukan
        `TransportRejectedError`, kecuali kredensialnya memang ditolak. Central
        yang sedang mati tidak boleh membuat batch masuk karantina hanya karena
        kebetulan tokennya perlu diperbarui saat itu.
        """
        async with self._auth_lock:
            try:
                response = await self._client.post(
                    "/api/v1/auth/device/token",
                    json={"client_id": self._client_id, "secret": self._secret},
                )
            except httpx.HTTPError as exc:
                raise TransportUnavailableError(
                    f"tidak bisa menjangkau central untuk menukar kredensial: {exc}",
                    details={"path": "/api/v1/auth/device/token"},
                ) from exc

            if response.status_code == 401:
                # Kredensial salah atau sudah dicabut. Ini TIDAK bisa diperbaiki
                # dengan mencoba ulang, dan mencoba terus hanya akan menabrak
                # rate limit auth di central.
                raise TransportRejectedError(
                    "central menolak kredensial device (HTTP 401) — "
                    "kemungkinan sudah dicabut atau salah disalin",
                    details={"status": 401, "client_id": self._client_id},
                )
            if response.status_code >= 400:
                raise TransportUnavailableError(
                    f"penukaran kredensial gagal (HTTP {response.status_code})",
                    details={"status": response.status_code, "body": response.text[:300]},
                )

            body = response.json()
            data = body.get("data", body)
            token = data.get("access_token")
            if not token:
                raise TransportRejectedError(
                    "jawaban penukaran kredensial tidak memuat access_token",
                    details={"body": str(data)[:300]},
                )
            self._token = str(token)
            self._counters["token_refresh"] += 1
            log.info("sync.token_refreshed", client_id=self._client_id)

    # -- ketersediaan -------------------------------------------------------

    async def is_available(self) -> bool:
        """Probe kesehatan sungguhan, bukan sekadar "interface up".

        Wi-Fi kapal yang berhasil associate tetapi tidak punya rute adalah kasus
        yang paling sering terjadi, dan pemeriksaan tingkat antarmuka akan
        melaporkannya sebagai sehat.
        """
        try:
            r = await self._client.get("/api/health", timeout=5.0)
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def health(self) -> TransportHealth:
        available = await self.is_available()
        return TransportHealth(
            kind=self.kind,
            available=available,
            detail=None if available else "central tidak bisa dijangkau",
            counters=dict(self._counters),
        )

    # -- pengiriman ---------------------------------------------------------

    async def send(self, envelope: BatchEnvelope, payload: bytes) -> TransportResult:
        if len(payload) <= self._single_shot_limit:
            return await self._send_single(envelope, payload)
        return await self._send_chunked(envelope, payload)

    async def _send_single(self, envelope: BatchEnvelope, payload: bytes) -> TransportResult:
        response = await self._request(
            "POST",
            "/api/v1/ingest/batches",
            content=payload,
            headers={
                "Content-Type": "application/octet-stream",
                "X-Batch-Envelope": envelope.model_dump_json(),
            },
        )
        ack = self._parse_ack(response, envelope)
        self._counters["sent"] += 1
        self._counters["bytes"] += len(payload)
        return TransportResult(ack=ack, bytes_sent=len(payload))

    async def _send_chunked(self, envelope: BatchEnvelope, payload: bytes) -> TransportResult:
        session = await self._open_session(envelope)

        # Server sudah punya batch ini secara utuh — kirim ulang ACK-nya, jangan
        # kirim ulang byte-nya. ACK yang hilang berbiaya satu round trip, bukan
        # satu upload ulang.
        if session.get("status") == "already_committed":
            ack = Ack.model_validate(session["ack"])
            log.info("sync.batch_already_committed", batch_id=str(envelope.batch_id))
            return TransportResult(ack=ack, bytes_sent=0, detail="sudah committed")

        session_id = session["session_id"]
        chunk_size = int(session.get("chunk_size") or self._chunk_size)
        received = set(session.get("received_chunks") or [])
        total = (len(payload) + chunk_size - 1) // chunk_size

        if received:
            self._counters["resumed"] += 1
            log.info(
                "sync.resuming_upload",
                batch_id=str(envelope.batch_id),
                already_received=len(received),
                total_chunks=total,
            )

        sent_bytes = 0
        for index in range(total):
            if index in received:
                continue  # sudah ada di server
            start = index * chunk_size
            chunk = payload[start : start + chunk_size]
            await self._request(
                "PUT",
                f"/api/v1/ingest/sessions/{session_id}/chunks/{index}",
                content=chunk,
                headers={"Content-Type": "application/octet-stream"},
            )
            sent_bytes += len(chunk)

        response = await self._request(
            "POST",
            f"/api/v1/ingest/sessions/{session_id}/commit",
            json={"payload_checksum": envelope.payload_checksum},
        )
        ack = self._parse_ack(response, envelope)
        self._counters["sent"] += 1
        self._counters["bytes"] += sent_bytes
        return TransportResult(ack=ack, bytes_sent=sent_bytes, resumed_from_chunk=len(received))

    async def _open_session(self, envelope: BatchEnvelope) -> dict[str, Any]:
        response = await self._request(
            "POST",
            "/api/v1/ingest/sessions",
            json=envelope.model_dump(mode="json"),
        )
        body: dict[str, Any] = response.json()
        data: dict[str, Any] = body.get("data", body)
        return data

    # -- HTTP ---------------------------------------------------------------

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = await self._send_request(method, path, **kwargs)

        # 401 pada permintaan yang membawa token biasanya berarti token itu
        # kedaluwarsa, bukan bahwa kredensialnya buruk. Tukar sekali lalu ulangi.
        #
        # Sekali, bukan dalam loop: kalau permintaan kedua tetap 401 dengan token
        # yang baru saja diterbitkan, masalahnya bukan kedaluwarsa, dan mengulang
        # terus hanya akan menabrak rate limit auth di central.
        if response.status_code == 401 and self._can_refresh:
            log.info("sync.token_kedaluwarsa", path=path)
            await self._obtain_token()
            response = await self._send_request(method, path, **kwargs)

        if response.status_code < 400:
            return response

        body = response.text[:500]
        details = {"status": response.status_code, "path": path, "body": body}

        # Aturan yang sama dengan seluruh sistem: 4xx berarti jangan diulang,
        # 5xx dan 429 berarti boleh. 429 saat 70 kapal tersambung kembali
        # bersamaan adalah respons yang memang diharapkan, bukan error.
        if response.status_code == 429 or response.status_code >= 500:
            raise TransportUnavailableError(
                f"central menolak sementara (HTTP {response.status_code})",
                details={**details, "retry_after": response.headers.get("Retry-After")},
            )
        raise TransportRejectedError(
            f"central menolak batch (HTTP {response.status_code}): {body}",
            details=details,
        )

    async def _send_request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        if not self._token and self._can_refresh:
            await self._obtain_token()
        headers = {**self._auth_header(), **kwargs.pop("headers", {})}
        try:
            return await self._client.request(method, path, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise TransportUnavailableError(
                f"tidak bisa menjangkau central: {exc}",
                details={"path": path},
            ) from exc

    @staticmethod
    def _parse_ack(response: httpx.Response, envelope: BatchEnvelope) -> Ack:
        body = response.json()
        data = body.get("data", body)
        try:
            ack = Ack.model_validate(data)
        except Exception as exc:
            raise TransportRejectedError(
                f"ACK dari central tidak bisa diurai: {exc}",
                details={"body": str(data)[:300]},
            ) from exc

        # Menandai batch synchronized berdasarkan ACK milik batch lain akan
        # menghapus data yang belum benar-benar sampai.
        if ack.batch_id != envelope.batch_id:
            raise TransportRejectedError(
                f"ACK merujuk batch lain: {ack.batch_id} bukan {envelope.batch_id}",
                details={"expected": str(envelope.batch_id), "got": str(ack.batch_id)},
            )
        return ack

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
