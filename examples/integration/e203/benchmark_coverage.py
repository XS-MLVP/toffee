"""A/B benchmark functional coverage on the real memory-direct e203 DUT."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import statistics
from tempfile import TemporaryDirectory

from e203_toffee_env import directed_requests, load_generated_dut, run_campaign


async def _run_once(DUT: type, coverage_file: Path, count: int, enabled: bool) -> float:
    dut = DUT(coverage_filename=str(coverage_file))
    try:
        _, summary = await run_campaign(
            dut,
            seed=0xE203,
            random_count=count,
            coverage_enabled=enabled,
        )
        return float(summary["elapsed_seconds"])
    finally:
        dut.Finish()


async def _benchmark(args: argparse.Namespace) -> dict:
    DUT = load_generated_dut(args.dut_dir)
    enabled: list[float] = []
    disabled: list[float] = []
    with TemporaryDirectory() as directory:
        root = Path(directory)
        for repeat in range(args.repeats):
            # Alternate order to reduce systematic thermal/load bias.
            order = (False, True) if repeat % 2 == 0 else (True, False)
            for state in order:
                elapsed = await _run_once(
                    DUT,
                    root / f"run-{repeat}-{int(state)}.dat",
                    args.random_count,
                    state,
                )
                (enabled if state else disabled).append(elapsed)
    enabled_median = statistics.median(enabled)
    disabled_median = statistics.median(disabled)
    slowdown = (enabled_median / disabled_median - 1.0) * 100.0
    return {
        "schema": 1,
        "random_transactions_per_run": args.random_count,
        "directed_transactions_per_run": len(directed_requests()),
        "repeats": args.repeats,
        "coverage_enabled_seconds": enabled,
        "coverage_disabled_seconds": disabled,
        "coverage_enabled_median_seconds": enabled_median,
        "coverage_disabled_median_seconds": disabled_median,
        "slowdown_percent": slowdown,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dut-dir", type=Path, required=True)
    parser.add_argument("--random-count", type=int, default=500)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--max-slowdown-percent", type=float)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.random_count < 0 or args.repeats <= 0:
        parser.error("random-count must be non-negative and repeats must be positive")
    report = asyncio.run(_benchmark(args))
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(
            f"coverage median={report['coverage_enabled_median_seconds']:.6f}s, "
            f"without={report['coverage_disabled_median_seconds']:.6f}s, "
            f"slowdown={report['slowdown_percent']:.2f}%"
        )
    limit = args.max_slowdown_percent
    if limit is not None and report["slowdown_percent"] > limit:
        print(f"slowdown exceeds --max-slowdown-percent {limit:.2f}%")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
