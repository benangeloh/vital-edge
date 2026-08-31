"""CLI impor USB.

fleetview-usb-import /media/usb0/SHIP-023 --url https://fleetview.example --token ...
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from fleetview_common import configure_logging
from fleetview_usb_import.importer import UsbImporter, verify_package


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fleetview-usb-import",
        description="Impor paket ekspor USB dari kapal ke Central Platform",
    )
    parser.add_argument("package", type=Path, help="folder paket, mis. /media/usb0/SHIP-023")
    parser.add_argument("--url", required=True, help="alamat Central Platform")
    parser.add_argument("--token", default="", help="token device")
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="hanya verifikasi checksum, jangan kirim apa pun",
    )
    args = parser.parse_args(argv)

    configure_logging(level="INFO", fmt="console", service="usb-import", version="0.1.0")

    valid, problems = verify_package(args.package)
    if not valid:
        print("Paket TIDAK valid:", file=sys.stderr)  # noqa: T201
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)  # noqa: T201
        return 1
    print(f"Paket terverifikasi: {args.package}")  # noqa: T201

    if args.verify_only:
        return 0

    async def run() -> int:
        importer = UsbImporter(base_url=args.url, token=args.token)
        try:
            result = await importer.import_package(args.package, verify=False)
        finally:
            await importer.close()

        print(  # noqa: T201
            f"{result.committed}/{result.total} batch tersimpan"
            + (f", {result.failed} gagal" if result.failed else "")
        )
        for problem in result.problems:
            print(f"  - {problem}", file=sys.stderr)  # noqa: T201
        return 0 if result.ok else 1

    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
