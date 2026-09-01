"""CLI simulator armada.

fleetview-simulate --ships 70 --sensors 100 --ticks 10
fleetview-simulate --json > benchmark.json
"""

from __future__ import annotations

import argparse
import sys

from fleetview_simulator.benchmark import run_benchmark


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fleetview-simulate",
        description="Benchmark beban skala armada dengan telemetry sintetis",
    )
    parser.add_argument("--ships", type=int, default=70)
    parser.add_argument("--sensors", type=int, default=100)
    parser.add_argument(
        "--ticks",
        type=int,
        default=10,
        help="putaran 1 Hz yang benar-benar dijalankan (bukan disimulasikan)",
    )
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--json", action="store_true", help="keluarkan JSON, bukan tabel")
    args = parser.parse_args(argv)

    result = run_benchmark(
        ships=args.ships,
        sensors_per_ship=args.sensors,
        ticks=args.ticks,
        batch_size=args.batch_size,
    )
    print(result.to_json() if args.json else result.render())  # noqa: T201
    return 0


if __name__ == "__main__":
    sys.exit(main())
