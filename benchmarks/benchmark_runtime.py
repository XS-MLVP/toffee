"""Reproducible microbenchmarks for the toffee/xcomm scheduling boundary.

Run with the same xspcomm build, simulator options and host load when comparing
results.  The synthetic XClock callback intentionally performs no DUT work.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import statistics
import time
from typing import Callable

import xspcomm

from toffee import (
    ClockCycles,
    FSM,
    RisingEdge,
    RunLimit,
    Execution,
    State,
    Value,
    XCommClockBackend,
    pytrigger,
    xtrigger,
)


class Dut:
    pass


@xtrigger(sample=RisingEdge("clk"))
def false_expr(dut):
    return dut.signal == 1


@xtrigger(sample=RisingEdge("clk"))
def false_fsm(dut):
    return FSM(
        start="WAIT",
        states={"WAIT": State().when(dut.signal == 1).trigger("MATCH")},
    )


@pytrigger(sample=RisingEdge("clk"))
def false_python(dut):
    return False


def make_dut() -> Dut:
    dut = Dut()
    dut.clk = xspcomm.XClock(lambda _: 0)
    dut.signal = xspcomm.XData(1, xspcomm.XData.InOut)
    dut.signal.Set(0)
    return dut


def measure(case: Callable[[int], None], ticks: int, repeats: int) -> dict:
    samples = []
    for _ in range(repeats):
        started = time.perf_counter_ns()
        case(ticks)
        samples.append(time.perf_counter_ns() - started)
    median_ns = statistics.median(samples)
    return {
        "median_ns": median_ns,
        "half_steps_per_second": ticks * 1_000_000_000 / median_ns,
        "samples_ns": samples,
    }


def raw_step(ticks: int) -> None:
    dut = make_dut()
    dut.clk.Step((ticks + 1) // 2)


def python_step_half(ticks: int) -> None:
    dut = make_dut()
    for _ in range(ticks):
        dut.clk.StepHalf()


def run_until(ticks: int, trigger_factory=None) -> None:
    dut = make_dut()
    backend = XCommClockBackend(dut.clk)
    if trigger_factory is not None:
        backend.arm(trigger_factory(dut))
    backend.run_until(
        # Disable the wall-clock quantum for throughput measurement.
        RunLimit(max_ticks=ticks)
    )
    backend.close()


def python_sampling(ticks: int) -> None:
    dut = make_dut()
    backend = XCommClockBackend(dut.clk)
    backend.arm(false_python(dut))
    advanced = 0
    while advanced < ticks:
        result = backend.run_until(
            RunLimit(max_ticks=ticks - advanced)
        )
        advanced += result.advanced_ticks
    backend.close()


async def reactor_await_once(count: int) -> None:
    dut = make_dut()
    backend = XCommClockBackend(dut.clk)
    async with Execution(backend, max_batch_ticks=4096):
        for _ in range(count):
            await ClockCycles(dut.clk, 1)


def reactor_await(ticks: int) -> None:
    asyncio.run(reactor_await_once(max(1, ticks // 2)))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticks", type=int, default=200_000)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.ticks <= 0 or args.repeats <= 0:
        parser.error("--ticks and --repeats must be positive")

    cases = {
        "raw_step_single_crossing": raw_step,
        "python_step_half": python_step_half,
        "run_until_no_watcher": lambda n: run_until(n),
        "run_until_value": lambda n: run_until(
            n, lambda dut: Value(dut.signal, 1, sample=RisingEdge(dut.clk))
        ),
        "run_until_expr": lambda n: run_until(n, false_expr),
        "run_until_fsm": lambda n: run_until(n, false_fsm),
        "pytrigger_each_sample": python_sampling,
        "reactor_await_each_cycle": reactor_await,
    }
    results = {
        name: measure(case, args.ticks, args.repeats)
        for name, case in cases.items()
    }
    report = {
        "schema": 1,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "ticks_per_case": args.ticks,
        "repeats": args.repeats,
        "results": results,
    }
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"ticks={args.ticks} repeats={args.repeats}")
        for name, result in results.items():
            rate = result["half_steps_per_second"]
            print(f"{name:28s} {rate:14,.0f} half-steps/s")


if __name__ == "__main__":
    main()
