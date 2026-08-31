"""Titik masuk Central API."""

from __future__ import annotations

import sys

import uvicorn

from fleetview_central.settings import load_settings
from fleetview_common import ConfigError


def main() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"config error [{exc.code}]: {exc.message}", file=sys.stderr)  # noqa: T201
        return 2

    uvicorn.run(
        "fleetview_central.main:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        log_config=None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
