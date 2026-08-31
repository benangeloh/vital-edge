"""Impor paket ekspor USB ke Central Platform.

Alat ini dijalankan di kantor setelah teknisi membawa flashdisk dari kapal.

Yang membuatnya sederhana adalah keputusan di Phase 4: berkas di dalam paket
**byte-nya identik** dengan yang akan dikirim lewat HTTP. Jadi alat ini tidak
punya jalur ingest sendiri — ia mem-POST berkas yang sama ke endpoint
`/api/v1/ingest` yang sama. Hanya ada satu jalur yang perlu diuji dan diamankan,
dan jalur USB yang paling jarang dipakai tidak menjadi yang paling jarang teruji.

Verifikasi dilakukan **sebelum** satu byte pun dikirim: manifest, checksum tiap
berkas, dan checksum manifest itu sendiri. Media lepas-pasang rusak diam-diam,
dan mengirim data rusak ke central lebih buruk daripada tidak mengirim apa pun.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from fleetview_common import ValidationError, get_logger
from fleetview_contracts import BatchEnvelope

__all__ = ["ImportResult", "UsbImporter", "verify_package"]

log = get_logger(__name__)

MANIFEST_NAME = "manifest.json"
TELEMETRY_DIR = "telemetry"


def _checksum(payload: bytes) -> str:
    import hashlib

    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _canonical(data: object) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")


def verify_package(root: Path) -> tuple[bool, list[str]]:
    """Verifikasi seluruh paket. Kembalikan (valid, daftar masalah)."""
    problems: list[str] = []
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file():
        return False, [f"{MANIFEST_NAME} tidak ada — ini bukan paket ekspor FleetView"]

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, [f"manifest tidak bisa dibaca: {exc}"]

    body = {k: v for k, v in manifest.items() if k != "export_checksum"}
    if _checksum(_canonical(body)) != manifest.get("export_checksum"):
        problems.append("checksum manifest tidak cocok — media kemungkinan rusak")

    for entry in manifest.get("batches", []):
        path = root / TELEMETRY_DIR / entry["filename"]
        if not path.is_file():
            problems.append(f"berkas batch hilang: {entry['filename']}")
            continue
        actual = _checksum(path.read_bytes())
        if actual != entry["payload_checksum"]:
            problems.append(
                f"checksum tidak cocok pada {entry['filename']}: "
                f"harapan {entry['payload_checksum']}, dapat {actual}"
            )

    return not problems, problems


@dataclass
class ImportResult:
    total: int = 0
    committed: int = 0
    duplicates: int = 0
    """Batch yang sudah ada di central. Bukan error — justru bukti idempotensi
    bekerja, dan wajar terjadi kalau kapal sempat sinkron online juga."""
    failed: int = 0
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.failed == 0


class UsbImporter:
    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            headers={"Authorization": f"Bearer {token}"} if token else {},
        )
        self._owns = client is None

    async def import_package(self, root: Path, *, verify: bool = True) -> ImportResult:
        """Impor satu paket ekspor.

        Args:
            root: folder paket, mis. `/media/usb0/SHIP-023`.
            verify: verifikasi checksum sebelum mengirim. Hanya matikan bila ada
                alasan yang sangat jelas — media rusak adalah alasan alat ini ada.
        """
        result = ImportResult()

        if verify:
            valid, problems = verify_package(root)
            if not valid:
                result.problems = problems
                result.failed = 1
                log.error("usb_import.verification_failed", problems=problems)
                return result

        manifest = json.loads((root / MANIFEST_NAME).read_text(encoding="utf-8"))
        entries = manifest.get("batches", [])
        result.total = len(entries)

        log.info(
            "usb_import.started",
            ship_id=manifest.get("ship_id"),
            batches=len(entries),
            records=manifest.get("record_count"),
        )

        for entry in entries:
            payload = (root / TELEMETRY_DIR / entry["filename"]).read_bytes()
            envelope = self._envelope_for(entry, manifest, len(payload))
            try:
                acked, duplicate = await self._send(envelope, payload)
            except Exception as exc:
                result.failed += 1
                result.problems.append(f"{entry['filename']}: {exc}")
                log.warning("usb_import.batch_failed", file=entry["filename"], error=str(exc))
                continue
            if acked:
                result.committed += 1
            if duplicate:
                result.duplicates += 1

        log.info(
            "usb_import.finished",
            committed=result.committed,
            duplicates=result.duplicates,
            failed=result.failed,
        )
        return result

    @staticmethod
    def _envelope_for(
        entry: dict[str, object], manifest: dict[str, object], size: int
    ) -> BatchEnvelope:
        return BatchEnvelope.model_validate(
            {
                "schema_version": manifest["schema_version"],
                "batch_id": entry["batch_id"],
                "ship_id": manifest["ship_id"],
                "device_id": manifest["device_id"],
                "sequence_start": entry["sequence_start"],
                "sequence_end": entry["sequence_end"],
                "first_timestamp": entry["first_timestamp"],
                "last_timestamp": entry["last_timestamp"],
                "record_count": entry["record_count"],
                "payload_checksum": entry["payload_checksum"],
                "payload_encoding": entry["payload_encoding"],
                "payload_size": size,
                "agent_version": manifest.get("agent_version") or "unknown",
                "config_version": "usb-import",
                "created_at": manifest["created_at"],
                "transport": "usb",
            }
        )

    async def _send(self, envelope: BatchEnvelope, payload: bytes) -> tuple[bool, bool]:
        """Kirim satu batch. Kembalikan (ter-ACK, adalah duplikat)."""
        response = await self._client.post(
            "/api/v1/ingest/batches",
            content=payload,
            headers={
                "Content-Type": "application/octet-stream",
                "X-Batch-Envelope": envelope.model_dump_json(),
            },
        )
        if response.status_code >= 400:
            raise ValidationError(
                f"central menolak (HTTP {response.status_code}): {response.text[:200]}",
                code="usb_import.rejected",
            )

        body = response.json()
        ack = body.get("data") or {}
        # Central memutar ulang ACK tersimpan untuk batch yang sudah committed.
        # Kita tidak bisa membedakannya dari ACK baru lewat kode status — dan
        # memang tidak perlu: keduanya berarti datanya sudah tersimpan.
        return bool(ack.get("batch_id")), False

    async def close(self) -> None:
        if self._owns:
            await self._client.aclose()
