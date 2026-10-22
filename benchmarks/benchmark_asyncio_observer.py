"""Measure simulation and unrelated host scheduling with the public Execution."""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import statistics
import time

import xspcomm

from toffee import ClockCycles, Execution, XCommClockBackend


async def run_case(case: str, args: argparse.Namespace) -> None:
    async def host() -> None:
        for _ in range(args.host_yields):
            await asyncio.sleep(0)

    if case == "host_without_execution":
        await asyncio.create_task(host())
        return

    clock = xspcomm.XClock(lambda _: 0)
    backend = XCommClockBackend(clock)
    try:
        async with Execution(backend) as execution:
            if case == "each_cycle":
                for _ in range(args.cycles):
                    await ClockCycles(clock, 1)
                assert backend.tick == 2 * args.cycles
            elif case == "batched":
                await ClockCycles(clock, args.batch_cycles)
                assert backend.tick == 2 * args.batch_cycles
            else:
                await execution.external_task(host())
                assert backend.tick == 0
    finally:
        backend.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycles", type=int, default=10_000)
    parser.add_argument("--batch-cycles", type=int, default=100_000)
    parser.add_argument("--host-yields", type=int, default=100_000)
    parser.add_argument("--repeats", type=int, default=7)
    args = parser.parse_args()
    if min(vars(args).values()) <= 0:
        parser.error("counts and repeats must be positive")

    cases = ("each_cycle", "batched", "host_with_execution", "host_without_execution")
    samples: dict[str, list[float]] = {case: [] for case in cases}
    for case in cases:
        asyncio.run(run_case(case, args))
    for repeat in range(args.repeats):
        for case in (cases if repeat % 2 == 0 else tuple(reversed(cases))):
            started = time.perf_counter_ns()
            asyncio.run(run_case(case, args))
            samples[case].append((time.perf_counter_ns() - started) / 1_000_000)
    results = {
        case: {"median_ms": statistics.median(values), "samples_ms": values}
        for case, values in samples.items()
    }
    print(json.dumps({
        "python": platform.python_version(),
        "backend": "native no-RTL XClock",
        "parameters": vars(args),
        "results": results,
        "external_overhead_percent": 100 * (
            results["host_with_execution"]["median_ms"]
            / results["host_without_execution"]["median_ms"] - 1
        ),
    }, indent=2))


if __name__ == "__main__":
    main()
