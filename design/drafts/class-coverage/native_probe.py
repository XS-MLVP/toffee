"""Runnable, deliberately small class-to-schema-to-C++ design probe.

This is NOT the implementation of api.pyi. Only named bins, point inheritance,
group overrides and simple Cross references are collected. Matching, statistics,
native lowering, lifecycle and reports use the unchanged xreactor implementation.
No Enum, generated C++ source, JIT compiler or per-bin Python callback is used.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import gc
import json
import platform
import statistics
import time
from typing import Generic, TypeVar

from xreactor import (
    Bin, ClockCycles, CoverGroupDef, CoverPointDef, CrossDef, Execution,
    RisingEdge, XCommClockBackend,
)
from xreactor.coverage import BinSpec

T = TypeVar("T")
C = TypeVar("C", bound=type)
HIGH = 1 << 100


class Point(Generic[T]):
    """Declaration marker only; instances contain no counters."""


def coverpoint(cls: C) -> C:
    if not issubclass(cls, Point):
        raise TypeError("probe coverpoint requires Point inheritance")
    return cls


def covergroup(*, schema_id: str, description: str = ""):
    def decorate(cls: C) -> C:
        cls._probe_schema_id = schema_id
        cls._probe_description = description
        return cls
    return decorate


@dataclass(frozen=True)
class Cross:
    points: tuple[Point, ...]


def declarations(model: type) -> tuple[dict, dict[int, str]]:
    members, aliases = {}, {}
    for base in reversed(model.__mro__):
        for name, value in vars(base).items():
            if name.startswith("_"):
                continue
            if isinstance(value, Point):
                if id(value) in aliases and aliases[id(value)] != name:
                    raise ValueError("point declaration reused under another slot")
                previous = members.get(name)
                if isinstance(previous, Point) and not issubclass(type(value), type(previous)):
                    raise ValueError("point override must specialize the original point")
                aliases[id(value)] = name
            members[name] = value
    return members, aliases


def compile_group(model: type) -> CoverGroupDef:
    """Collect declarations; all semantic validation remains in existing schema."""
    if any(len(base.__bases__) > 1 for base in model.__mro__):
        raise ValueError("probe supports single inheritance only; fragments need a full compiler")
    members, aliases = declarations(model)
    points, crosses = [], []
    for name, value in sorted(members.items()):
        if isinstance(value, Point):
            bins = {}
            for base in reversed(type(value).__mro__):
                if any(key in bins and not isinstance(rule, BinSpec)
                       for key, rule in vars(base).items()):
                    raise ValueError("probe does not support removing inherited bins")
                bins.update({key: rule for key, rule in vars(base).items()
                             if isinstance(rule, BinSpec)})
            points.append(CoverPointDef(name, dict(sorted(bins.items())), overlap="allow"))
        elif isinstance(value, Cross):
            try:
                slots = tuple(aliases[id(point)] for point in value.points)
            except KeyError as error:
                raise ValueError("cross references a point outside this model") from error
            if any(not isinstance(members[slot], Point) for slot in slots):
                raise ValueError("cross references a replaced non-point slot")
            crosses.append(CrossDef(name, slots))
    return CoverGroupDef(model._probe_schema_id, tuple(points), tuple(crosses),
                         description=model._probe_description)


def lower_wires(model: type, wires: tuple[tuple[Point, object], ...]) -> dict:
    """String keys exist only in lowering to the current runtime API."""
    _, aliases = declarations(model)
    result = {}
    for point, signal in wires:
        name = aliases[id(point)]
        if name in result:
            raise ValueError("duplicate signal binding")
        result[name] = signal
    return result


@coverpoint
class StatePoint(Point[int]):
    idle = Bin.values(0)
    busy = Bin.values(1, at_least=2)
    done = Bin.values(2)
    small = Bin.range(3, 5)
    high_mask = Bin.masked(0x80, 0xF0, width=8)
    path = Bin.transition(0, 1, 2)
    held = Bin.transition(1, 1)
    ignored = Bin.ignore(Bin.values(254))
    illegal = Bin.illegal(Bin.values(255))
    other = Bin.default()


@coverpoint
class FlagPoint(Point[int]):
    zero = Bin.values(0)
    one = Bin.values(1)


@coverpoint
class PayloadPoint(Point[int]):
    zero = Bin.values(0)
    high = Bin.masked(HIGH, HIGH, width=128)
    other = Bin.default()


@covergroup(schema_id="draft.native.pipeline", description="Class compilation probe")
class PipelineCoverage:
    state = StatePoint()
    flag = FlagPoint()
    payload = PayloadPoint()
    state_flag = Cross((state, flag))


class SplitStatePoint(StatePoint):
    small = Bin.range(3, 4)
    extra = Bin.values(5)


class SplitPipelineCoverage(PipelineCoverage):
    state = SplitStatePoint()


class NegativeStatePoint(StatePoint):
    negative = Bin.values(-1)


class NegativePipelineCoverage(PipelineCoverage):
    state = NegativeStatePoint()


@dataclass(frozen=True)
class Sample:
    state: int
    flag: int
    payload: int


TRACE = tuple(Sample(state, index & 1, HIGH + 1 if index & 1 else 0)
              for index, state in enumerate((0, 1, 2, 1, 1, 3, 5, 0x81, 254, 255, 7)))


def direct_schema() -> CoverGroupDef:
    """Independent hand-written equivalent, not obtained from class collection."""
    return CoverGroupDef("draft.native.pipeline", (
        CoverPointDef("flag", {"one": Bin.values(1), "zero": Bin.values(0)}, overlap="allow"),
        CoverPointDef("payload", {
            "high": Bin.masked(HIGH, HIGH, width=128), "other": Bin.default(), "zero": Bin.values(0),
        }, overlap="allow"),
        CoverPointDef("state", {
            "busy": Bin.values(1, at_least=2), "done": Bin.values(2), "held": Bin.transition(1, 1),
            "high_mask": Bin.masked(0x80, 0xF0, width=8), "idle": Bin.values(0),
            "ignored": Bin.ignore(Bin.values(254)), "illegal": Bin.illegal(Bin.values(255)),
            "other": Bin.default(), "path": Bin.transition(0, 1, 2), "small": Bin.range(3, 5),
        }, overlap="allow"),
    ), (CrossDef("state_flag", ("state", "flag")),), description="Class compilation probe")


def semantic_report(report: dict) -> dict:
    """Compare statistics; run identity, provenance and backend metadata differ."""
    result = {key: report[key] for key in ("schema_digest", "samples", "gated", "coverage",
                                          "covered", "has_illegal", "collection_complete")}
    for category in ("points", "crosses"):
        result[category] = [{key: value for key, value in item.items() if key != "provenance"}
                            for item in report[category]]
    result["illegal_hits"] = [{key: hit[key] for key in ("item", "bin", "value")}
                              for hit in report["illegal_hits"]]
    return result


async def run_bound(model: type, strategy: str, *, trace=TRACE, cycles: int | None = None,
                    schema: CoverGroupDef | None = None, baseline: bool = False) -> dict:
    import xspcomm
    clock = xspcomm.XClock(lambda _: 0)
    backend = XCommClockBackend(clock)
    signals = tuple(xspcomm.XData(width, xspcomm.XData.InOut) for width in (8, 1, 128))
    group = (schema or compile_group(model)).instantiate("draft.pipeline", illegal_policy="record")
    fields = lower_wires(model, tuple(zip((model.state, model.flag, model.payload), signals)))
    group.bind(trigger=RisingEdge(clock), fields=fields, strategy=strategy)
    returns = 0
    original_run = backend.run_until

    def counted(limit):
        nonlocal returns
        returns += 1
        return original_run(limit)

    backend.run_until = counted
    for signal, value in zip(signals, (1, 1, HIGH + 1)):
        signal.Set(value)
    started = time.perf_counter_ns()
    try:
        async with Execution(backend, coverage=[] if baseline else [group],
                             max_batch_ticks=2 * (cycles or len(trace)) + 2, quantum_ms=1000):
            if cycles is not None:
                await ClockCycles(clock, cycles)
            else:
                for sample in trace:
                    for signal, value in zip(signals, (sample.state, sample.flag, sample.payload)):
                        signal.Set(value)
                    await ClockCycles(clock, 1)
                before = group.report()
                assert group.report() == before, "snapshot was counted twice"
        elapsed = time.perf_counter_ns() - started
        return {"elapsed_ns": elapsed, "run_until_returns": returns, "report": group.report()}
    finally:
        try:
            assert backend._engine.ActiveCount() == 0, "native registration leaked on exit/failure"
        finally:
            backend.close()


def verify() -> dict:
    import xspcomm
    base = compile_group(PipelineCoverage)
    assert base.to_dict() == direct_schema().to_dict()
    saved = base.to_dict()
    checked = []
    for model in (PipelineCoverage, SplitPipelineCoverage):
        schema = compile_group(model)
        manual = schema.instantiate("manual", illegal_policy="record")
        for sample in TRACE:
            manual.sample(sample, details=False)
        python = asyncio.run(run_bound(model, "python"))["report"]
        native = asyncio.run(run_bound(model, "native"))["report"]
        assert semantic_report(manual.report()) == semantic_report(python) == semantic_report(native)
        assert native["sampling_backend"] == "native" and python["sampling_backend"] == "python"
        state = next(point for point in native["points"] if point["name"] == "state")
        assert state["counts"]["path"] == state["counts"]["held"] == 1
        assert state["counts"]["illegal"] == state["counts"]["ignored"] == 1
        checked.append(model.__name__)
    assert compile_group(PipelineCoverage).to_dict() == saved
    split = compile_group(SplitPipelineCoverage).instantiate("split").report()
    assert any("extra" in bin_["tuple"] for bin_ in split["crosses"][0]["bins"])
    try:
        asyncio.run(run_bound(NegativePipelineCoverage, "native"))
    except NotImplementedError as error:
        reason = str(error)
        assert "unsigned" in reason
    else:
        raise AssertionError("strict native accepted an unsupported negative constant")
    fallback = asyncio.run(run_bound(NegativePipelineCoverage, "auto"))["report"]
    assert fallback["sampling_backend"] == "python" and "unsigned" in fallback["sampling_fallback"]
    reference = compile_group(NegativePipelineCoverage).instantiate("reference", illegal_policy="record")
    for sample in TRACE:
        reference.sample(sample, details=False)
    assert semantic_report(reference.report()) == semantic_report(fallback)
    return {"coverage_abi": xspcomm.XTriggerEngine.CoverageVersion(),
            "models": checked, "trace_samples": len(TRACE),
            "statistics_equal": True, "strict_native_rejection": reason,
            "auto_fallback": fallback["sampling_fallback"]}


def benchmark(cycles: int, repeats: int) -> dict:
    started = time.perf_counter_ns()
    compiled = compile_group(PipelineCoverage)
    compile_ns = time.perf_counter_ns() - started
    direct = direct_schema()
    modes = ("baseline", "python", "native_direct", "native_class")
    samples = {mode: [] for mode in modes}

    def run(mode, count):
        result = asyncio.run(run_bound(
            PipelineCoverage, "python" if mode == "python" else "native", cycles=count,
            schema=direct if mode == "native_direct" else compiled, baseline=mode == "baseline"))
        if mode != "baseline":
            assert result["report"]["samples"] == count
            state = next(point for point in result["report"]["points"] if point["name"] == "state")
            assert state["counts"]["busy"] == count and state["counts"]["held"] == count - 1
        if mode.startswith("native"):
            assert result["run_until_returns"] == 1, "coverage forced per-sample returns"
        return result

    for mode in modes:
        run(mode, min(cycles, 100))
    for repeat in range(repeats):
        offset = repeat % len(modes)
        for mode in modes[offset:] + modes[:offset]:
            gc.collect()
            result = run(mode, cycles)
            samples[mode].append((result["elapsed_ns"], result["run_until_returns"]))
    return {"cycles": cycles, "repeats": repeats, "schema_compile_ns": compile_ns,
            "results": {mode: {
                "ns_per_cycle": statistics.median(elapsed for elapsed, _ in rows) / cycles,
                "samples_ns": [elapsed for elapsed, _ in rows],
                "run_until_returns": [count for _, count in rows],
            } for mode, rows in samples.items()}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", action="store_true")
    parser.add_argument("--cycles", type=int, default=10_000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.cycles < 2 or args.repeats < 1:
        parser.error("cycles must be >= 2 and repeats must be >= 1")
    result = {"python": platform.python_version(), "platform": platform.platform(),
              "verification": verify()}
    if args.benchmark:
        result["benchmark"] = benchmark(args.cycles, args.repeats)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print("PASS: class/direct schema; manual/Python/native statistics; inherited Cross; strict/auto; cleanup")
        if args.benchmark:
            for mode, item in result["benchmark"]["results"].items():
                print(f"{mode:15s} {item['ns_per_cycle']:10.1f} ns/cycle; RunUntil returns {item['run_until_returns']}")


if __name__ == "__main__":
    main()
