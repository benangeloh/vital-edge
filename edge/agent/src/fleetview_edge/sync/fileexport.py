"""FileExportTransport — export ke USB/HDD.

Menghasilkan paket portabel yang bisa dibawa teknisi ke kantor:

    SHIP-023/
      manifest.json      identitas, rentang, jumlah, checksum keseluruhan
      telemetry/         satu berkas per batch, byte-nya IDENTIK dengan HTTP
      checksums/         sha256 per berkas, untuk memverifikasi media

Dua sifat yang menentukan desain ini:

**Byte-nya identik dengan yang dikirim lewat HTTP.** Bukan format kedua, bukan
jalur ingest kedua. Alat impor di kantor mem-POST berkas-berkas ini ke endpoint
`/api/v1/ingest` yang sama persis. Jadi hanya ada satu jalur yang perlu diuji
dan satu yang perlu diamankan — dan jalur USB, yang paling jarang dipakai, tidak
menjadi yang paling jarang teruji.

**Export tidak pernah menghapus data lokal.** Berkas yang tertulis ke flashdisk
bukan bukti central sudah menerimanya. Sampai ACK sungguhan kembali, batch tetap
pending. Flashdisk yang hilang atau rusak karena itu tidak menghilangkan apa pun.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from fleetview_common import get_logger, now_utc, uuid7
from fleetview_contracts import BatchEnvelope
from fleetview_edge.outbox import canonical_json, checksum_of
from fleetview_edge.sync.base import (
    TransportAdapter,
    TransportHealth,
    TransportKind,
    TransportRejectedError,
    TransportResult,
    TransportUnavailableError,
)

__all__ = ["FileExportTransport"]

log = get_logger(__name__)

MANIFEST_NAME = "manifest.json"
TELEMETRY_DIR = "telemetry"
CHECKSUM_DIR = "checksums"
MIN_FREE_BYTES = 16 * 1024 * 1024


class FileExportTransport(TransportAdapter):
    """Menulis batch ke folder export di media lepas-pasang.

    Args:
        target_dir: mount point USB/HDD.
        ship_slug: nama folder, mis. `SHIP-023`.
    """

    kind = TransportKind.USB

    def __init__(self, *, target_dir: Path | str, ship_slug: str) -> None:
        self._target = Path(target_dir)
        self._slug = ship_slug
        self._export_id = uuid7()
        self._counters = {"batches": 0, "bytes": 0}

    @property
    def export_root(self) -> Path:
        return self._target / self._slug

    # -- ketersediaan -------------------------------------------------------

    async def is_available(self) -> bool:
        """Media terpasang, bisa ditulis, dan ruangnya cukup.

        Ketiganya diperiksa. Flashdisk yang terpasang read-only atau nyaris
        penuh akan gagal di tengah export, dan itu jauh lebih membingungkan bagi
        teknisi daripada ditolak sejak awal.
        """
        try:
            if not self._target.is_dir():
                return False
            usage = shutil.disk_usage(self._target)
            if usage.free < MIN_FREE_BYTES:
                return False
            probe = self._target / ".fleetview_write_test"
            probe.write_bytes(b"")
            probe.unlink()
        except OSError:
            return False
        return True

    async def health(self) -> TransportHealth:
        available = await self.is_available()
        detail = None
        if not available:
            detail = f"{self._target} tidak terpasang, tidak bisa ditulis, atau penuh"
        return TransportHealth(
            kind=self.kind, available=available, detail=detail, counters=dict(self._counters)
        )

    # -- pengiriman ---------------------------------------------------------

    async def send(self, envelope: BatchEnvelope, payload: bytes) -> TransportResult:
        """Tulis satu batch ke paket export.

        Mengembalikan `TransportResult` dengan `ack=None`. Itu disengaja dan
        penting: berkas sudah tertulis, tetapi tidak ada yang mengonfirmasi
        central menerimanya. Sync Engine karena itu **tidak** akan menandai
        batch ini synchronized.
        """
        if not await self.is_available():
            raise TransportUnavailableError(
                f"media export tidak siap: {self._target}",
                details={"target": str(self._target)},
            )

        root = self.export_root
        telemetry = root / TELEMETRY_DIR
        checksums = root / CHECKSUM_DIR
        try:
            telemetry.mkdir(parents=True, exist_ok=True)
            checksums.mkdir(parents=True, exist_ok=True)

            name = f"{envelope.batch_id}-{envelope.sequence_start}-{envelope.sequence_end}.fvb"
            # Tulis ke berkas sementara lalu ganti nama. Kalau flashdisk dicabut
            # di tengah penulisan, yang tertinggal adalah berkas .tmp yang jelas
            # tidak lengkap — bukan berkas .fvb yang terlihat sah tapi terpotong.
            tmp = telemetry / f"{name}.tmp"
            tmp.write_bytes(payload)
            tmp.replace(telemetry / name)

            (checksums / f"{name}.sha256").write_text(
                f"{envelope.payload_checksum}  {name}\n", encoding="utf-8"
            )
            self._write_manifest(root, envelope, name, len(payload))
        except OSError as exc:
            raise TransportRejectedError(
                f"gagal menulis paket export: {exc}",
                details={"target": str(root)},
            ) from exc

        self._counters["batches"] += 1
        self._counters["bytes"] += len(payload)
        log.info(
            "sync.batch_exported",
            batch_id=str(envelope.batch_id),
            file=name,
            bytes=len(payload),
            note="belum ada ACK: batch tetap pending sampai central mengonfirmasi",
        )
        return TransportResult(
            ack=None,
            bytes_sent=len(payload),
            detail="tertulis ke media export; menunggu ACK",
        )

    def _write_manifest(
        self, root: Path, envelope: BatchEnvelope, filename: str, size: int
    ) -> None:
        """Perbarui manifest secara inkremental.

        Manifest ditulis ulang setiap batch, bukan hanya di akhir. Kalau
        flashdisk dicabut di tengah export, yang tertinggal tetap paket yang
        sah dan bisa diimpor — hanya lebih sedikit isinya.
        """
        path = root / MANIFEST_NAME
        if path.is_file():
            manifest = json.loads(path.read_text(encoding="utf-8"))
        else:
            manifest = {
                "export_id": str(self._export_id),
                "ship_id": str(envelope.ship_id),
                "device_id": str(envelope.device_id),
                "schema_version": envelope.schema_version,
                "agent_version": envelope.agent_version,
                "created_at": now_utc().isoformat(),
                "batches": [],
            }

        manifest["batches"].append(
            {
                "batch_id": str(envelope.batch_id),
                "filename": filename,
                "sequence_start": envelope.sequence_start,
                "sequence_end": envelope.sequence_end,
                "record_count": envelope.record_count,
                "payload_checksum": envelope.payload_checksum,
                "payload_encoding": envelope.payload_encoding,
                "payload_size": size,
                "first_timestamp": envelope.first_timestamp.isoformat(),
                "last_timestamp": envelope.last_timestamp.isoformat(),
            }
        )

        batches = manifest["batches"]
        manifest["record_count"] = sum(b["record_count"] for b in batches)
        manifest["first_timestamp"] = min(b["first_timestamp"] for b in batches)
        manifest["last_timestamp"] = max(b["last_timestamp"] for b in batches)

        # Checksum keseluruhan dihitung atas manifest tanpa field ini sendiri,
        # supaya media yang rusak ketahuan sebelum kita mempercayai satu byte pun.
        manifest.pop("export_checksum", None)
        manifest["export_checksum"] = checksum_of(canonical_json(manifest))

        tmp = root / f"{MANIFEST_NAME}.tmp"
        tmp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        tmp.replace(path)

    @staticmethod
    def verify_package(root: Path) -> tuple[bool, list[str]]:
        """Verifikasi paket export. Dipakai alat impor sebelum ingest.

        Mengembalikan (valid, daftar masalah).
        """
        problems: list[str] = []
        manifest_path = root / MANIFEST_NAME
        if not manifest_path.is_file():
            return False, [f"{MANIFEST_NAME} tidak ada"]

        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return False, [f"manifest tidak bisa dibaca: {exc}"]

        expected = manifest.get("export_checksum")
        body = {k: v for k, v in manifest.items() if k != "export_checksum"}
        if checksum_of(canonical_json(body)) != expected:
            problems.append("checksum manifest tidak cocok — media kemungkinan rusak")

        for entry in manifest.get("batches", []):
            path = root / TELEMETRY_DIR / entry["filename"]
            if not path.is_file():
                problems.append(f"berkas batch hilang: {entry['filename']}")
                continue
            actual = checksum_of(path.read_bytes())
            if actual != entry["payload_checksum"]:
                problems.append(
                    f"checksum tidak cocok pada {entry['filename']}: "
                    f"harapan {entry['payload_checksum']}, dapat {actual}"
                )

        return not problems, problems
