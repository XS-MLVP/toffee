"""Class coverage on a freshly generated picker Adder DUT (native ABI 3)."""
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
from pathlib import Path
import sys

from toffee import (
    Bundle, ClockCycles, ConditionMode, Execution, FSM, RisingEdge,
    Sequence, State, Wait, Within, XCommClockBackend, xtrigger,
)
from toffee.declarative import Bin, SignalCoverGroup, TemporalCoverPoint, covergroup
from toffee.ir import signal_expr
from examples.coverage.declarative_protocol import Pin


class AdderPins(Bundle):
    clock: object
    a: Pin
    b: Pin
    cin: Pin
    sum: Pin
    cout: Pin

    def __init__(self, clock: object, *, a: Pin, b: Pin, cin: Pin, sum: Pin, cout: Pin):
        super().__init__(a=a, b=b, cin=cin, sum=sum, cout=cout)
        self.clock = clock


@xtrigger(sample=RisingEdge("clock"))
def addition_result(pins: AdderPins):
    active = ((signal_expr(pins.a) != 0) | (signal_expr(pins.b) != 0)
              | (signal_expr(pins.cin) != 0))
    return FSM(start="idle", states={
        "idle": State().when(active).goto("observing"),
        "observing": State().when(signal_expr(pins.cout) == 1).trigger("CARRY")
            .when(signal_expr(pins.cout) == 0).trigger("NO_CARRY"),
    })


@xtrigger(sample=RisingEdge("clock"))
def full_width_overflow(pins: AdderPins):
    return Sequence(
        Wait((signal_expr(pins.a) == (1 << 128) - 1) & (signal_expr(pins.b) == 1)),
        Within(1, 1, (signal_expr(pins.cout) == 1) & (signal_expr(pins.sum) == 0)),
    )


@xtrigger(sample=RisingEdge("clock"), mode=ConditionMode.EACH_SAMPLE)
def high_result(pins: AdderPins):
    return signal_expr(pins.sum) >= 1 << 127


@xtrigger(sample=RisingEdge("clock"), mode=ConditionMode.EACH_SAMPLE)
def correct_wide_addition(pins: AdderPins):
    expected = (signal_expr(pins.a).resize(129) + signal_expr(pins.b).resize(129)
                + signal_expr(pins.cin).resize(129))
    actual = signal_expr(pins.sum).resize(129) + (signal_expr(pins.cout).resize(129) << 128)
    return expected == actual


@xtrigger(sample=RisingEdge("clock"), mode=ConditionMode.EACH_SAMPLE)
def negative_signed_result(pins: AdderPins):
    return signal_expr(pins.sum).as_signed() < 0


class Results(TemporalCoverPoint[AdderPins]):
    carry = Bin.pattern(addition_result, terminals=("CARRY",))
    no_carry = Bin.pattern(addition_result, terminals=("NO_CARRY",))
    all = Bin.pattern(addition_result, all_terminals=True)


class Overflow(TemporalCoverPoint[AdderPins]):
    observed = Bin.pattern(full_width_overflow)
    repeated = Bin.pattern(full_width_overflow, at_least=2)


class UpperHalf(TemporalCoverPoint[AdderPins]):
    observed = high_result
    repeated = Bin.pattern(high_result, at_least=2)


class Arithmetic(TemporalCoverPoint[AdderPins]):
    correct = correct_wide_addition
    signed_negative = negative_signed_result


@covergroup(schema_id="picker.adder", contract="picker.adder-stable-two-samples/v1")
class AdderCoverage(SignalCoverGroup[AdderPins]):
    results = Results()
    overflow = Overflow()
    upper_half = UpperHalf()
    arithmetic = Arithmetic()


async def exercise(dut):
    """Hold each addition for two samples, then insert an idle sample.

    The RTL is combinational. The FSM and Sequence describe the verification
    observation window; they do not assert that the adder has a pipeline.
    """
    pins = AdderPins(dut.xclock, a=dut.a, b=dut.b, cin=dut.cin, sum=dut.sum, cout=dut.cout)
    backend = XCommClockBackend(dut.xclock)
    coverage = AdderCoverage(instance="dut.adder")
    coverage.bind(pins=pins, sample=RisingEdge(dut.xclock), strategy="native")
    mask = (1 << 128) - 1
    async with Execution(backend, coverage=[coverage.runtime]):
        # Inspect the draft's native resource count before arming a waiter.
        collector = coverage.runtime._collector
        resources = {"bins": len(collector.bin_patterns),
                     "executions": backend._engine.CoverageExecutionCount(collector._native_handle()),
                     "registrations": backend._engine.ActiveCount()}
        for a, b, cin in ((1, 2, 0), (mask, 1, 0), (1 << 127, 0, 0),
                          (1 << 127, 1 << 127, 1)):
            pins.a.Set(a)
            pins.b.Set(b)
            pins.cin.Set(cin)
            await ClockCycles(dut.xclock, 2)
            expected = a + b + cin
            assert pins.sum.U() == expected & mask
            assert pins.cout.U() == expected >> 128
            pins.a.Set(0)
            pins.b.Set(0)
            pins.cin.Set(0)
            await ClockCycles(dut.xclock, 1)
    assert coverage.results.count(Results.carry) == 2
    assert coverage.results.count(Results.no_carry) == 2
    assert coverage.results.count(Results.all) == 4
    assert coverage.overflow.count(Overflow.observed) == 1
    assert coverage.overflow.count(Overflow.repeated) == 1
    assert coverage.upper_half.count(UpperHalf.observed) == 2
    assert coverage.upper_half.count(UpperHalf.repeated) == 2
    assert coverage.arithmetic.count(Arithmetic.correct) == 12
    assert coverage.arithmetic.count(Arithmetic.signed_negative) == 2
    assert resources == {"bins": 9, "executions": 5, "registrations": 1}
    return coverage.report(), resources


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dut-path", type=Path, required=True,
                        help="directory containing the freshly generated Adder package")
    parser.add_argument("--dut-module", default="Adder",
                        help="Python import path (Adder.UT_Adder when retaining VERBOSE=ON files)")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(args.dut_path.resolve()))
    generated = importlib.import_module(args.dut_module)
    assert generated.xsp.abi_version() == 3
    assert generated.xsp.XTriggerEngine.CoverageVersion() == 5
    dut = generated.DUTAdder()
    try:
        report, resources = asyncio.run(exercise(dut))
    finally:
        dut.Finish()
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"backend": report["sampling_backend"], "resources": resources,
                      "counts": {point["name"]: point["counts"] for point in report["points"]}}, indent=2))


if __name__ == "__main__":
    main()
