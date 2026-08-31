"""Titik masuk Edge Agent.

Dijalankan systemd di kapal:  `fleetview-edge --config /etc/fleetview/edge.yaml`
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn

from fleetview_common import ConfigError, get_logger
from fleetview_edge.app import EdgeAgent
from fleetview_edge.settings import load_settings
from fleetview_edge.version import AGENT_VERSION

log = get_logger(__name__)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="fleetview-edge",
        description="FleetView Edge Agent — data logger di kapal",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="berkas config YAML (default: FLEETVIEW_EDGE_CONFIG lalu env vars saja)",
    )
    parser.add_argument("--version", action="version", version=AGENT_VERSION)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    try:
        settings = load_settings(args.config)
    except ConfigError as exc:
        # Logging belum terpasang di titik ini, jadi tulis langsung ke stderr.
        print(f"config error [{exc.code}]: {exc.message}", file=sys.stderr)  # noqa: T201
        return 2

    agent = EdgeAgent(settings)
    agent.setup()

    if not settings.console.enabled:
        log.warning("edge_agent.console_disabled")
        log.info("edge_agent.idle", reason="collector belum ada sampai Phase 2")
        return 0

    log.info(
        "edge_agent.console_starting",
        host=settings.console.host,
        port=settings.console.port,
    )
    uvicorn.run(
        agent.build_console(),
        host=settings.console.host,
        port=settings.console.port,
        log_config=None,  # structlog sudah memegang kendali output
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
