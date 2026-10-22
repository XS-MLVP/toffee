"""Small functional-coverage definition organized around one protocol."""

import argparse
import asyncio
from types import SimpleNamespace

from toffee import (Bin, ClockCycles, CoverageDatabase, CoverGroupDef, CoverPointDef,
                      CrossDef, Execution, MemoryBackend, RisingEdge, XCommClockBackend,
                      Sequence, Wait, Within, xtrigger)


fetch_coverage = CoverGroupDef(
    "fetch",
    (
        CoverPointDef(
            "target",
            {
                "local": Bin.values("local"),
                "external": Bin.values("external"),
            },
            description="Exercises both instruction-fetch destinations.",
        ),
        CoverPointDef(
            "response",
            {
                "ok": Bin.values("ok"),
                "error": Bin.values("error"),
            },
            description="Exercises successful and failed responses.",
        ),
    ),
    (
        CrossDef(
            "target_x_response",
            ("target", "response"),
            description="Checks every response type on both routes.",
        ),
    ),
    description="End-to-end fetch routing coverage.",
)


async def state_coverage_example(native=False):
    """A protocol-independent state trace, with isolated and accumulated runs."""
    if native:
        import xspcomm
        state = xspcomm.XData(8, xspcomm.XData.InOut)
        clock = xspcomm.XClock(lambda _: 0)
        backend = XCommClockBackend(clock)
    else:
        state = SimpleNamespace(value=0, width=8)
        clock = object()
        backend = MemoryBackend(clock)
    group = CoverGroupDef("state", (CoverPointDef("state", {
        "idle": Bin.values(0), "busy": Bin.values(1), "done": Bin.values(2),
        "idle_to_done": Bin.transition(0, 1, 2),
        "busy_two_samples": Bin.transition(1, 1),
    }),)).instantiate("dut.state")
    group.bind(trigger=RisingEdge(clock), fields={"state": state},
               strategy="native" if native else "python")
    try:
        async with Execution(backend, coverage=[group]):
            for value in (0, 1, 2, 1, 1):
                if native:
                    state.Set(value)
                else:
                    state.value = value
                await ClockCycles(clock, 1)
            group.sync()  # explicit checkpoint; report() also synchronizes
            assert group.samples == 5
            assert group.report() == group.report()
        group.assert_coverage()
        # Configure deliberate accumulation. Partial sequences are still reset
        # at each Execution boundary, and native handles are recreated.
        group.bind(trigger=RisingEdge(clock), fields={"state": state},
                   strategy="native" if native else "python", accumulate=True)
        async with Execution(backend, coverage=[group]):
            await ClockCycles(clock, 1)
        assert group.samples == 6
        assert group.report()["points"][0]["counts"]["busy_two_samples"] == 1
        return group
    finally:
        backend.close()


async def wide_coverage_example(native=False):
    """The same 128-bit trace can be counted in C++ or sampled in Python."""
    import xspcomm
    payload = xspcomm.XData(128, xspcomm.XData.InOut)
    clock = xspcomm.XClock(lambda _: 0)
    backend = XCommClockBackend(clock)
    high = 1 << 100
    group = CoverGroupDef("wide_payload", (CoverPointDef("payload", {
        "exact": Bin.values(high + 1),
        "range": Bin.range(high, high + 3),
        "mask": Bin.masked(high + 1, high + 3, width=128),
        "path": Bin.transition(high + 1, high + 2),
        "other": Bin.default(),
    }, overlap="allow"),)).instantiate("dut.payload")
    group.bind(trigger=RisingEdge(clock), fields={"payload": payload},
               strategy="native" if native else "python")
    try:
        async with Execution(backend, coverage=[group]):
            for value in (high + 1, high + 2, 1):
                payload.Set(hex(value))  # XData string assignment preserves every bit
                await ClockCycles(clock, 1)
        assert group.report()["points"][0]["counts"] == {
            "exact": 1, "range": 2, "mask": 1, "path": 1, "other": 1}
        group.assert_coverage()
        return group
    finally:
        backend.close()


@xtrigger(sample=RisingEdge("clock"))
def bounded_completion(dut):
    return Sequence(Wait(dut.start), Within(1, 3, dut.done))


async def overlap_coverage_example(native=False):
    """Two starts share a later completion condition; neither is a transaction ID."""
    if native:
        import xspcomm
        clock = xspcomm.XClock(lambda _: 0)
        backend = XCommClockBackend(clock)
        signals = {name: xspcomm.XData(8, xspcomm.XData.InOut) for name in ("start", "done", "value")}
    else:
        clock = object()
        backend = MemoryBackend(clock)
        signals = {name: SimpleNamespace(value=0, width=8) for name in ("start", "done", "value")}
    dut = SimpleNamespace(clock=clock, **signals)
    group = CoverGroupDef("completions", (CoverPointDef("value", {
        "observed": Bin.values(0),
    }),)).instantiate("dut.pipeline")
    group.bind(trigger=bounded_completion(dut), fields=signals,
               overlap=True, max_active=2, diagnostics="summary",
               strategy="native" if native else "python")
    try:
        async with Execution(backend, coverage=[group]):
            for cycle, (start, done) in enumerate(((1, 0), (1, 0), (0, 1)), 1):
                for name, value in (("start", start), ("done", done)):
                    if native:
                        signals[name].Set(value)
                    else:
                        signals[name].value = value
                await ClockCycles(clock, 1)
                if cycle == 2:
                    assert len(group.inspect()["trigger"]) == 2
        assert group.samples == 2
        summary = group.report()["diagnostics"]["patterns"][0]
        assert summary["started"] == summary["completed"] == summary["peak_active"] == 2
        assert summary["unfinished_at_close"] == 0
        return group
    finally:
        backend.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", help="save a reusable functional coverage JSON artifact")
    scenario = parser.add_mutually_exclusive_group()
    scenario.add_argument("--stateful", action="store_true", help="run the state-transition example")
    scenario.add_argument("--wide", action="store_true", help="run the 128-bit XClock example")
    scenario.add_argument("--overlap", action="store_true", help="run bounded overlapping patterns with diagnostics")
    parser.add_argument("--native", action="store_true", help="count coverage in xcomm")
    args = parser.parse_args()
    if args.native and not (args.stateful or args.wide or args.overlap):
        parser.error("--native requires --stateful, --wide or --overlap")
    if args.overlap:
        coverage = asyncio.run(overlap_coverage_example(args.native))
    elif args.wide:
        coverage = asyncio.run(wide_coverage_example(args.native))
    elif args.stateful:
        coverage = asyncio.run(state_coverage_example(args.native))
    else:
        coverage = fetch_coverage.instantiate("core0.ifu")
        coverage.sample({"target": "local", "response": "ok"})
        coverage.sample({"target": "local", "response": "error"})
        coverage.sample({"target": "external", "response": "ok"})
        coverage.sample({"target": "external", "response": "error"})
    coverage.assert_coverage()
    if args.output:
        CoverageDatabase([coverage]).write_json(args.output)
    print(coverage.report())
