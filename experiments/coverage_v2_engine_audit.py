"""Reproduce coverage v2 lifecycle and expression limits; draft, not benchmarks.

Run from the repository root with the draft xcomm Python package available:
    python -m experiments.coverage_v2_engine_audit --output /tmp/audit.json

Resource queries measure DAG nodes and constants, not process RSS. Run against
the matching native draft; collection may relocate roots, so indices are not counts.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import xspcomm as x

from xreactor import bits, ClockCycles, Execution, RisingEdge, RunLimit, XCommClockBackend, xtrigger
from xreactor.ir import _evaluate_condition, signal_expr
from xreactor.triggers import CompiledTrigger
from examples.coverage.declarative_protocol import ToyResponder
from examples.coverage.trigger_bins import Completions, ProtocolCoverage


@xtrigger(sample=RisingEdge("clock"))
def expected_value(dut, *, value: int):
    return signal_expr(dut.value) == value


async def lifecycle(waits: int, failure_attempts: int):
    toy = ToyResponder(engine="xcomm")
    toy.clock.RemoveStepRisCbByDesc("_evaluate_native")
    backend = toy.backend
    value = x.XData(32, x.XData.InOut)
    dut = SimpleNamespace(clock=toy.clock, value=value)
    coverage = ProtocolCoverage(instance="audit")
    coverage.bind(pins=toy.pins, sample=RisingEdge(toy.clock), strategy="native")
    rows = []
    peak_nodes = 0
    try:
        async with Execution(backend, coverage=[coverage.runtime]):
            request, response = toy.pins.request, toy.pins.response
            request.valid.Set(1)
            request.ready.Set(1)
            request.tag.Set(1)
            await ClockCycles(toy.clock, 1)
            request.valid.Set(0)
            # Keep the coverage FSM pending while many short waiters come/go.
            for start in range(0, waits, 500):
                stop = min(start + 500, waits)
                for expected in range(start, stop):
                    value.Set(expected)
                    await expected_value.with_args(value=expected)(dut)
                    peak_nodes = max(peak_nodes, backend._engine.ExpressionNodeCount())
                rows.append({"completed_waits": stop,
                             "program_cache_size": backend.program_cache_size,
                             "registrations": backend.watcher_count,
                             "native_nodes": backend._engine.ExpressionNodeCount()})
            backend.collect_programs()
            collected_nodes = backend._engine.ExpressionNodeCount()
            before = backend._engine.ExpressionNodeCount()
            for _ in range(64):
                await expected_value.with_args(value=waits - 1)(dut)
            repeated_nodes = backend._engine.ExpressionNodeCount() - before
            before = backend._engine.ExpressionNodeCount()
            invalid = CompiledTrigger("invalid", None,
                (signal_expr(value) == 0) & (signal_expr(value) == -1),
                sample=RisingEdge(toy.clock))
            for _ in range(failure_attempts):
                try:
                    backend.arm(invalid)
                except ValueError as error:
                    compile_error = str(error)
                else:
                    raise AssertionError("negative constants unexpectedly supported")
            failed_nodes = backend._engine.ExpressionNodeCount() - before
            response.valid.Set(1)
            response.ready.Set(1)
            response.tag.Set(1)
            response.data.Set(10)
            await ClockCycles(toy.clock, 1)
            completed = coverage.completions.count(Completions.correct_data)
            assert completed == 1, "persistent FSM must retain its pending state"
        return {"samples": rows, "peak_nodes": peak_nodes, "nodes_after_explicit_collection": collected_nodes,
                "same_program_repeated_64_times_new_nodes": repeated_nodes,
                "partial_compile_failures": failure_attempts,
                "partial_compile_new_nodes": failed_nodes, "compile_error": compile_error,
                "pending_coverage_fsm_completions": completed,
                "after_execution": {"cache": backend.program_cache_size,
                                    "registrations": backend.watcher_count,
                                    "nodes": backend._engine.ExpressionNodeCount()}}
    finally:
        toy.close()


def capacity_failure(attempts: int):
    clock = x.XClock(lambda _: 0)
    backend = XCommClockBackend(clock, capacity=1)
    value = x.XData(32, x.XData.InOut)
    dut = SimpleNamespace(clock=clock, value=value)
    blocker = backend.arm(RisingEdge(clock))
    try:
        before = backend._engine.ExpressionNodeCount()
        for expected in range(attempts):
            try:
                backend.arm(expected_value.with_args(value=expected)(dut))
            except RuntimeError as error:
                assert "capacity exhausted" in str(error)
            else:
                raise AssertionError("watcher capacity unexpectedly available")
        return {"failed_arms": attempts, "cache": backend.program_cache_size,
                "new_nodes": backend._engine.ExpressionNodeCount() - before,
                "registrations": backend.watcher_count}
    finally:
        backend.disarm(blocker)
        backend.close()


def expression_limits():
    clock = x.XClock(lambda _: 0)
    backend = XCommClockBackend(clock)
    narrow = x.XData(8, x.XData.InOut)
    narrow.Set(255)
    wide = x.XData(128, x.XData.InOut)
    wide.Set(1 << 127)
    engine = x.ExprEngine()
    narrow_root, one = engine.NewSignal(narrow), engine.NewConst(1)
    results = {
        "legacy_native_scalar_8bit_255_plus_1": engine.Eval(engine.NewBinary(x.ExprOp_ADD, narrow_root, one)),
        "legacy_native_scalar_1_shift_left_64": engine.Eval(engine.NewBinary(
            x.ExprOp_SHL, one, engine.NewConst(64))),
        "unsigned_8bit_ff_less_than_zero": _evaluate_condition(signal_expr(narrow) < 0, None),
    }
    def native_match(condition):
        handle = backend.arm(CompiledTrigger("audit-expression", None, condition,
                                           sample=RisingEdge(clock)))
        try:
            return bool(backend.run_until(RunLimit(max_ticks=2)).hits)
        finally:
            backend.disarm(handle)

    try:
        for name, condition in (("negative_constant", signal_expr(narrow) == -1),
                                ("wide_direct_compare", signal_expr(wide) == 1 << 127),
                                ("typed_8bit_wrap", (signal_expr(narrow) + 1) == 0),
                                ("typed_shift_at_width", (bits(1, 64) << 64) == 0),
                                ("signed_negative", signal_expr(narrow).as_signed() < 0),
                                ("sign_extend", signal_expr(narrow).as_signed().resize(128) == -1),
                                ("zero_extend", signal_expr(narrow).resize(128) == 255),
                                ("wide_add", (signal_expr(wide) + 1) == (1 << 127) + 1)):
            try:
                results[name] = {"matched": native_match(condition)}
            except (TypeError, ValueError) as error:
                results[name] = {"error": str(error)}
        a, b = x.XData(8, x.XData.InOut), x.XData(8, x.XData.InOut)
        a.Set(0xF0)
        b.Set(0x0F)
        results["and_is_logical"] = {"predicate_result": _evaluate_condition(signal_expr(a) & signal_expr(b), None),
                                     "integer_bitwise_result": 0xF0 & 0x0F}
        return results
    finally:
        backend.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--waits", type=int, default=2000)
    parser.add_argument("--failure-attempts", type=int, default=64)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.waits <= 0 or args.failure_attempts <= 0:
        parser.error("wait and failure counts must be positive")
    report = {"native_abi": x.abi_version(), "coverage_protocol": x.XTriggerEngine.CoverageVersion(),
              "lifecycle": asyncio.run(lifecycle(args.waits, args.failure_attempts)),
              "capacity_failure": capacity_failure(args.failure_attempts),
              "expression_limits": expression_limits()}
    rendered = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    print(rendered, end="")


if __name__ == "__main__":
    main()
