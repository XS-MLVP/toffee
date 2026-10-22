"""Measure transaction-level functional-coverage sampling overhead."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import gc
import json
import platform
import statistics
import time
from typing import Callable
from types import SimpleNamespace

from toffee import Bin, CoverGroupDef, CoverPointDef, CrossDef, RisingEdge, Sequence, Wait, Within, xtrigger


@xtrigger(sample=RisingEdge("clock"))
def overlapping_pattern(dut):
    return Sequence(Wait(dut.state == 1), Within(3, 3, dut.state == 1))


@dataclass(frozen=True, slots=True)
class Transaction:
    target: str
    alignment: str
    response: str
    commands: int
    command_bp: bool
    response_bp: bool


SCHEMA = CoverGroupDef(
    "coverage_benchmark",
    (
        CoverPointDef("target", {"itcm": Bin.values("itcm"), "biu": Bin.values("biu")}),
        CoverPointDef(
            "alignment",
            {name: Bin.values(name) for name in ("begin", "middle", "cross")},
        ),
        CoverPointDef("response", {"ok": Bin.values("ok"), "error": Bin.values("error")}),
        CoverPointDef(
            "commands",
            {"zero": Bin.values(0), "one": Bin.values(1), "two": Bin.values(2)},
        ),
        CoverPointDef("command_bp", {"no": Bin.values(False), "yes": Bin.values(True)}),
        CoverPointDef("response_bp", {"no": Bin.values(False), "yes": Bin.values(True)}),
    ),
    (
        CrossDef("target_x_alignment", ("target", "alignment")),
        CrossDef("target_x_response", ("target", "response")),
        CrossDef("backpressure", ("command_bp", "response_bp")),
    ),
)

TRANSACTIONS = tuple(
    Transaction(
        "itcm" if index & 1 else "biu",
        ("begin", "middle", "cross")[index % 3],
        "error" if index % 17 == 0 else "ok",
        index % 3,
        bool(index & 2),
        bool(index & 4),
    )
    for index in range(256)
)


def _baseline(iterations: int) -> int:
    checksum = 0
    for index in range(iterations):
        item = TRANSACTIONS[index & 255]
        checksum += item.commands + item.command_bp + item.response_bp
    return checksum


def _coverage(iterations: int, *, details: bool) -> int:
    group = SCHEMA.instantiate("bench")
    for index in range(iterations):
        group.sample(TRANSACTIONS[index & 255], details=details)
    return group.samples


def _measure(operation: Callable[[int], int], iterations: int, repeats: int) -> dict:
    samples: list[int] = []
    operation(min(iterations, 2_000))
    for _ in range(repeats):
        gc.collect()
        gc.disable()
        try:
            started = time.perf_counter_ns()
            result = operation(iterations)
            elapsed = time.perf_counter_ns() - started
        finally:
            gc.enable()
        if result <= 0:
            raise RuntimeError("benchmark operation produced no observable result")
        samples.append(elapsed)
    median = statistics.median(samples)
    return {
        "median_ns": median,
        "ns_per_transaction": median / iterations,
        "transactions_per_second": iterations * 1_000_000_000 / median,
        "samples_ns": samples,
    }


def measure_native(cycles: int, repeats: int) -> dict:
    import xspcomm
    from toffee import ClockCycles, Execution, RisingEdge, XCommClockBackend

    async def run(mode):
        clock = xspcomm.XClock(lambda _: 0)
        backend = XCommClockBackend(clock)
        state = xspcomm.XData(8, xspcomm.XData.InOut)
        state.Set(1)
        bins = {"busy": Bin.values(1)}
        if mode == "native_transition":
            bins["held"] = Bin.transition(1, 1, 1)
        group = CoverGroupDef("native_bench", (CoverPointDef("state", bins),)).instantiate("dut")
        overlap = mode in ("native_overlap", "native_overlap_summary")
        trigger = (overlapping_pattern(SimpleNamespace(clock=clock, state=state))
                   if overlap else RisingEdge(clock))
        group.bind(trigger=trigger, fields={"state": state},
                   strategy="python" if mode == "python" else "native",
                   overlap=overlap, max_active=8 if overlap else None,
                   diagnostics="summary" if mode == "native_overlap_summary" else "off")
        returns = 0
        original = backend.run_until
        def counted(limit):
            nonlocal returns
            result = original(limit)
            returns += 1
            return result
        backend.run_until = counted
        started = time.perf_counter_ns()
        try:
            async with Execution(backend, coverage=[] if mode == "baseline" else [group],
                                 max_batch_ticks=2 * cycles, quantum_ms=60_000):
                await ClockCycles(clock, cycles)
            elapsed = time.perf_counter_ns() - started
            if mode != "baseline":
                expected = cycles - 3 if overlap else cycles
                assert group.samples == expected
                assert group.report()["points"][0]["counts"]["busy"] == expected
            if mode == "native_overlap_summary":
                summary = group.report()["diagnostics"]["patterns"][0]
                assert summary["started"] == cycles
                assert summary["completed"] == cycles - 3
                assert summary["peak_active"] == summary["unfinished_at_close"] == 3
            if mode == "native_transition":
                assert group.report()["points"][0]["counts"]["held"] == cycles - 2
            if mode != "python":
                assert returns == 1, "coverage caused extra RunUntil returns"
            return elapsed, returns
        finally:
            backend.close()

    modes = ("baseline", "python", "native_static", "native_transition",
             "native_overlap", "native_overlap_summary")
    measurements = {mode: [] for mode in modes}
    for _ in range(repeats):
        for mode in modes:
            measurements[mode].append(asyncio.run(run(mode)))
    return {mode: {"samples_ns": [time for time, _ in samples],
                   "ns_per_cycle": statistics.median(time for time, _ in samples) / cycles,
                   "run_until_returns": [count for _, count in samples]}
            for mode, samples in measurements.items()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=100_000)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--native", action="store_true",
                        help="compare passive native and Python cycle sampling")
    parser.add_argument(
        "--max-fast-ns",
        type=float,
        help="optional CI failure threshold for details=False",
    )
    args = parser.parse_args()
    if args.iterations <= 0 or args.repeats <= 0:
        parser.error("iterations and repeats must be positive")
    if args.native:
        if args.iterations < 4:
            parser.error("native pattern benchmark requires at least 4 cycles")
        print(json.dumps({"cycles": args.iterations, "repeats": args.repeats,
                          "results": measure_native(args.iterations, args.repeats)}, indent=2))
        return 0

    results = {
        "transaction_access_baseline": _measure(_baseline, args.iterations, args.repeats),
        "coverage_fast": _measure(
            lambda count: _coverage(count, details=False), args.iterations, args.repeats
        ),
        "coverage_with_details": _measure(
            lambda count: _coverage(count, details=True), args.iterations, args.repeats
        ),
    }
    baseline = results["transaction_access_baseline"]["ns_per_transaction"]
    for name in ("coverage_fast", "coverage_with_details"):
        results[name]["incremental_ns"] = results[name]["ns_per_transaction"] - baseline
    report = {
        "schema": 1,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "iterations": args.iterations,
        "repeats": args.repeats,
        "coverpoints": len(SCHEMA.points),
        "crosses": len(SCHEMA.crosses),
        "results": results,
    }
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(
            f"iterations={args.iterations} repeats={args.repeats} "
            f"points={len(SCHEMA.points)} crosses={len(SCHEMA.crosses)}"
        )
        for name, result in results.items():
            print(
                f"{name:30s} {result['ns_per_transaction']:10.1f} ns/transaction "
                f"{result['transactions_per_second']:12,.0f} transactions/s"
            )
    fast_ns = results["coverage_fast"]["ns_per_transaction"]
    if args.max_fast_ns is not None and fast_ns > args.max_fast_ns:
        print(
            f"coverage_fast {fast_ns:.1f} ns exceeds --max-fast-ns "
            f"{args.max_fast_ns:.1f}"
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
