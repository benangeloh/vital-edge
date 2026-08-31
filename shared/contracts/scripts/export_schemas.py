#!/usr/bin/env python3
"""Hasilkan JSON Schema dari model Pydantic.

**Arah generasi: Pydantic adalah sumbernya, JSON Schema hasilnya.**

Rencana awal di Phase 0 menulis arah sebaliknya (JSON Schema -> Pydantic + TS).
Dibalik karena satu alasan praktis: validasi sungguhan berjalan di Python, di
kedua sisi (edge dan central). Menaruh sumber kebenaran di JSON Schema berarti
validator kustom — rentang sequence yang harus berurutan, penolakan datetime
naif, disiplin slug — hidup terpisah dari schema dan bisa menyimpang darinya.
Dengan Pydantic sebagai sumber, aturan dan schema tidak bisa berbeda.

CI menjalankan skrip ini lalu memeriksa `git diff`; artefak hasil generasi yang
basi akan menggagalkan build.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_DIR = REPO_ROOT / "shared" / "contracts" / "schemas"

sys.path.insert(0, str(REPO_ROOT / "shared" / "contracts" / "src"))

from fleetview_contracts import (  # noqa: E402
    SCHEMA_VERSION,
    Ack,
    BatchEnvelope,
    ExportManifest,
    Heartbeat,
    HeartbeatResponse,
    Reading,
    SyncState,
    UploadSession,
)

MODELS: dict[str, Any] = {
    "reading": Reading,
    "batch-envelope": BatchEnvelope,
    "ack": Ack,
    "sync-state": SyncState,
    "upload-session": UploadSession,
    "heartbeat": Heartbeat,
    "heartbeat-response": HeartbeatResponse,
    "export-manifest": ExportManifest,
}


def _render(name: str, model: Any) -> str:
    schema = model.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"https://fleetview.spil.co.id/schemas/{SCHEMA_VERSION}/{name}.json"
    # sort_keys + newline di akhir menjaga diff tetap stabil, sehingga
    # pemeriksaan drift hanya menyala saat schema benar-benar berubah.
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="jangan tulis apa pun; keluar dengan kode 1 bila berkas di disk sudah basi",
    )
    args = parser.parse_args(argv)

    SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    stale: list[str] = []

    for name, model in MODELS.items():
        rendered = _render(name, model)
        target = SCHEMA_DIR / f"{name}.schema.json"

        if args.check:
            # Perbandingan langsung isi berkas, bukan lewat git — sehingga cek ini
            # tetap benar di repo yang belum punya commit dan di working tree kotor.
            current = target.read_text(encoding="utf-8") if target.is_file() else None
            if current != rendered:
                stale.append(str(target.relative_to(REPO_ROOT)))
            continue

        target.write_text(rendered, encoding="utf-8")
        print(f"  {target.relative_to(REPO_ROOT)}")

    if args.check:
        if stale:
            print("JSON Schema sudah basi:", file=sys.stderr)
            for path in stale:
                print(f"  {path}", file=sys.stderr)
            print("Jalankan 'make schemas' lalu commit hasilnya.", file=sys.stderr)
            return 1
        print(f"{len(MODELS)} schema sinkron dengan model Pydantic.")
        return 0

    print(f"{len(MODELS)} schema ditulis (schema_version={SCHEMA_VERSION})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
