"""Window expiry and broken holds must agree on memory and native engines."""

from types import SimpleNamespace

import pytest

from xreactor import (
    AnyOf, Execution, FSM, Hold, MemoryBackend, RisingEdge, Sequence, SimTimeout,
    State, Wait, Within, XCommClockBackend, XEventKind, XPhase, xtrigger,
)


@xtrigger(sample=RisingEdge("clk"))
def pattern(dut):
    return Sequence(Wait(dut.req), Within(2, 3, dut.ack), Hold(dut.stable, cycles=2))


@xtrigger(sample=RisingEdge("clk"))
def priority(dut):
    return FSM(start="select", states={
        "select": State().when(dut.req).trigger("FIRST").when(dut.ack).trigger("SECOND"),
    })


@pytest.mark.asyncio
@pytest.mark.parametrize("backend_kind", ["memory", "native"])
@pytest.mark.parametrize("trigger,requests,acks,stable,match_cycle,terminal", [
    pytest.param(pattern, {1}, {2, 4}, {5, 6}, 6, "MATCHED", id="early-ack-ignored-upper-bound-included"),
    pytest.param(pattern, {1, 6}, {5, 8}, {9, 10}, 10, "MATCHED", id="expired-window-restarts-at-request"),
    pytest.param(pattern, {1}, {3}, {4, 6, 7}, 7, "MATCHED", id="broken-hold-restarts-streak-only"),
    pytest.param(priority, {1}, {1}, set(), 1, "FIRST", id="first-matching-fsm-branch-wins"),
])
async def test_compiled_pattern_contract(backend_kind, trigger, requests, acks, stable, match_cycle, terminal):
    if backend_kind == "native":
        xspcomm = pytest.importorskip("xspcomm")
        signals = [xspcomm.XData(1, xspcomm.XData.InOut) for _ in range(3)]
    else:
        signals = [SimpleNamespace(value=0) for _ in range(3)]

    def sample(phase, tick):
        if phase is XPhase.RISING_STABLE:
            for signal, cycles in zip(signals, (requests, acks, stable)):
                value = int(tick // 2 in cycles)
                if backend_kind == "native":
                    signal.Set(value)
                else:
                    signal.value = value

    if backend_kind == "native":
        clock = xspcomm.XClock(lambda _: 0)
        def native_sample(cycle):
            sample(XPhase.RISING_STABLE, cycle * 2)
        clock.StepRis(native_sample)
        backend = XCommClockBackend(clock)
    else:
        clock = object()
        backend = MemoryBackend(clock, on_phase=sample)
    dut = SimpleNamespace(clk=clock, req=signals[0], ack=signals[1], stable=signals[2])
    try:
        async with Execution(backend):
            event = await AnyOf(trigger(dut), SimTimeout(20, clock=clock))
        assert event.kind is XEventKind.FSM
        assert event.tick == match_cycle * 2
        assert event.terminal_state == terminal
        assert backend.watcher_count == 0
    finally:
        backend.close()
        if backend_kind == "native":
            clock.RemoveStepRisCbByDesc("native_sample")

# Coverage uses the same native clock and compiled patterns as ordinary waits.
from xreactor import Bin, CoverGroupDef, CoverPointDef, CrossDef, ClockCycles, Iff, Next


def coverage_backend(kind, names=("value", "other", "gate", "abort")):
    if kind == "memory":
        clock = object()
        return MemoryBackend(clock), {name: SimpleNamespace(value=0, width=8) for name in names}
    xspcomm = pytest.importorskip("xspcomm")
    clock = xspcomm.XClock(lambda _: 0)
    return XCommClockBackend(clock), {name: xspcomm.XData(8, xspcomm.XData.InOut) for name in names}


def set_coverage_signal(signal, value):
    if hasattr(signal, "Set"):
        signal.Set(value)
    else:
        signal.value = value


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,strategy", [("memory", "auto"), ("native", "native"), ("native", "python")])
async def test_coverage_exclusion_identity_and_run_evidence(kind, strategy):
    from xreactor import CoverGroup, CoverageMergeError
    backend, signals = coverage_backend(kind)
    group = CoverGroupDef("identity", (
        CoverPointDef("value", {"a": Bin.values(0), "a × b": Bin.values(1),
                                 "dead": Bin.values(2), "ignored": Bin.ignore(Bin.values(2))}),
        CoverPointDef("other", {"b × c": Bin.values(0), "c": Bin.values(1)}),
    ), (CrossDef("together", ("value", "other")),)).instantiate(
        "dut", run_id="test-native-evidence", run_metadata={"seed": 17},
    ).bind(trigger=RisingEdge(backend.clock), fields=signals, strategy=strategy)
    try:
        async with Execution(backend, coverage=[group]):
            await ClockCycles(backend.clock, 1)
            before = group.report()
            assert before["crosses"][0]["coverage"] == 25
            for name in ("value", "other"):
                set_coverage_signal(signals[name], 1)
            await ClockCycles(backend.clock, 1)
            report = group.report()
        assert report["points"][0]["coverage"] == 100
        assert report["crosses"][0]["coverage"] == 50
        assert len(report["crosses"][0]["counts"]) == 4
        for item in (report["points"][0], report["crosses"][0]):
            for name, sources in item["provenance"].items():
                proof = sources[report["active_origin"]]
                assert proof["count"] == item["counts"][name] == 1
                if strategy == "native":
                    assert proof["first"] is proof["last"] is None
                else:
                    assert proof["first"]["tick"] in (2, 4)
        restored = CoverGroup.from_report(before)
        with pytest.raises(CoverageMergeError, match="duplicate|overlapping"):
            restored.merge(group)
        assert restored.samples == 1
    finally:
        backend.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,strategy", [("memory", "auto"), ("native", "native"), ("native", "python")])
async def test_coverage_execution_isolation_sync_and_native_batching(kind, strategy):
    backend, signals = coverage_backend(kind)
    group = CoverGroupDef("state", (CoverPointDef("value", {
        "one": Bin.values(1), "two": Bin.values(2), "path": Bin.transition(1, 2),
    }),)).instantiate("dut").bind(trigger=RisingEdge(backend.clock), fields=signals, strategy=strategy)
    calls = []
    run = backend.run_until
    def counted(limit):
        result = run(limit)
        calls.append(result)
        return result
    backend.run_until = counted
    try:
        set_coverage_signal(signals["value"], 1)
        async with Execution(backend, coverage=[group]):
            await ClockCycles(backend.clock, 7)
            first = group.report()
            assert first == group.report()  # cumulative snapshot is not a new shard
            first["sampling_contract"]["fields"]["value"] = 16
            assert group.report()["sampling_contract"]["fields"]["value"] == 8
            assert first["samples"] == 7
            assert first["points"][0]["counts"]["one"] == 7
            with pytest.raises(RuntimeError, match="manually sample"):
                group.sample({"value": 1})
        assert backend.watcher_count == 0
        assert backend._coverage_collectors == []
        with pytest.raises(RuntimeError, match="manually sample"):
            group.sample({"value": 1})
        if strategy == "native":
            assert len(calls) == 1 and calls[0].advanced_ticks == 14
        set_coverage_signal(signals["value"], 2)
        async with Execution(backend, coverage=[group]):
            await ClockCycles(backend.clock, 1)
        assert group.samples == 1
        assert group.report()["points"][0]["counts"] == {"one": 0, "two": 1, "path": 0}
        group.bind(trigger=RisingEdge(backend.clock), fields=signals, strategy=strategy, accumulate=True)
        set_coverage_signal(signals["value"], 1)
        async with Execution(backend, coverage=[group]):
            await ClockCycles(backend.clock, 1)
        set_coverage_signal(signals["value"], 2)
        async with Execution(backend, coverage=[group]):
            await ClockCycles(backend.clock, 1)
            assert group.report()["points"][0]["counts"] == {"one": 1, "two": 2, "path": 0}
            group.reset()
            assert group.report()["samples"] == 0
            await ClockCycles(backend.clock, 2)
        assert group.report()["points"][0]["counts"]["two"] == 2
    finally:
        backend.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,strategy", [("memory", "auto"), ("native", "native"), ("native", "python")])
async def test_coverage_transitions_cross_gate_abort_and_cleanup(kind, strategy):
    backend, signals = coverage_backend(kind)
    group = CoverGroupDef("state", (
        CoverPointDef("value", {"same": Bin.transition(1, 1), "path": Bin.transition(1, 2),
                                 "ignored": Bin.ignore(Bin.values(9))}, iff=Iff.equals("gate", 1)),
        CoverPointDef("other", {"zero": Bin.values(0)}),
    ), (CrossDef("together", ("value", "other")),)).instantiate("dut")
    group.bind(trigger=RisingEdge(backend.clock), fields=signals, abort=signals["abort"], strategy=strategy)
    try:
        async with Execution(backend, coverage=[group]):
            for value, gate, abort in [(1, 1, 0), (1, 1, 0), (1, 1, 0), (2, 1, 0),
                                      (1, 1, 0), (0, 0, 0), (2, 1, 0),
                                      (1, 1, 0), (2, 1, 1), (2, 1, 0),
                                      (1, 1, 0), (9, 1, 0), (2, 1, 0)]:
                for name, val in (("value", value), ("gate", gate), ("abort", abort)):
                    set_coverage_signal(signals[name], val)
                await ClockCycles(backend.clock, 1)
            counts = group.report()
            assert counts["points"][0]["counts"] == {"same": 2, "path": 1, "ignored": 1}
            assert counts["crosses"][0]["counts"] == {'["same","zero"]': 2, '["path","zero"]': 1}
            assert counts["samples"] == 12
        assert backend.watcher_count == 0
        assert group._collector is None and not group._history
        # Passive collectors never request advancement on their own.
        tick = backend.tick
        async with Execution(backend, coverage=[group]):
            import asyncio
            await asyncio.sleep(0)
        assert backend.tick == tick
    finally:
        backend.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "native"])
async def test_coverage_start_failure_and_body_exception_release_ownership(kind):
    backend, signals = coverage_backend(kind)
    schema = CoverGroupDef("state", (CoverPointDef("value", {"zero": Bin.values(0)}),))
    good = schema.instantiate("good").bind(trigger=RisingEdge(backend.clock), fields=signals)
    bad = schema.instantiate("bad").bind(trigger=RisingEdge(backend.clock), fields={"missing": signals["value"]})
    try:
        with pytest.raises(ValueError, match="unbound"):
            async with Execution(backend, coverage=[good, bad]):
                pass
        assert backend.watcher_count == 0 and not backend._coverage_collectors
        error = ValueError("body failed")
        with pytest.raises(ValueError) as raised:
            async with Execution(backend, coverage=[good]):
                await ClockCycles(backend.clock, 3)
                raise error
        assert raised.value is error
        assert good.samples == 3 and good._collector is None
        assert backend.watcher_count == 0 and backend._owner is None
    finally:
        backend.close()


@xtrigger(sample=RisingEdge("clock"))
def adjacent_coverage_pattern(dut):
    return Sequence(Wait(dut.value == 1), Next(dut.value == 2))


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,strategy", [("memory", "auto"), ("native", "native"), ("native", "python")])
async def test_pattern_coverage_persists_and_abort_precedes_completion(kind, strategy):
    backend, signals = coverage_backend(kind)
    dut = SimpleNamespace(clock=backend.clock, **signals)
    group = CoverGroupDef("completed", (CoverPointDef("value", {"two": Bin.values(2)}),)).instantiate("dut")
    group.bind(trigger=adjacent_coverage_pattern(dut), fields=signals,
               abort=signals["abort"], strategy=strategy)
    try:
        async with Execution(backend, coverage=[group]):
            for value, abort in [(1, 0), (2, 0), (1, 0), (9, 0), (2, 0),
                                 (1, 0), (2, 1), (2, 0), (1, 0), (2, 0)]:
                set_coverage_signal(signals["value"], value)
                set_coverage_signal(signals["abort"], abort)
                await ClockCycles(backend.clock, 1)
        assert group.samples == 2
        assert group.report()["points"][0]["counts"]["two"] == 2
        assert backend.watcher_count == 0
    finally:
        backend.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "native"])
async def test_coverage_external_cancellation_and_snapshot_failure_cleanup(kind):
    import asyncio
    backend, signals = coverage_backend(kind)
    group = CoverGroupDef("state", (CoverPointDef("value", {"zero": Bin.values(0)}),)).instantiate("dut")
    group.bind(trigger=RisingEdge(backend.clock), fields=signals)
    outside = asyncio.create_task(asyncio.Event().wait())
    started = asyncio.Event()
    async def simulation():
        async with Execution(backend, coverage=[group]):
            await ClockCycles(backend.clock, 2)
            started.set()
            await asyncio.Event().wait()
    task = asyncio.create_task(simulation())
    try:
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert group.samples == 2 and not group._history
        assert not outside.done()
        assert backend.watcher_count == 0 and backend._owner is None
        failure = RuntimeError("snapshot failed")
        with pytest.raises(RuntimeError, match="snapshot failed"):
            async with Execution(backend, coverage=[group]):
                await ClockCycles(backend.clock, 1)
                def broken_sync():
                    raise failure
                group._collector.sync = broken_sync
        assert group._collector is None
        assert backend.watcher_count == 0 and backend._owner is None
    finally:
        outside.cancel()
        await asyncio.gather(outside, return_exceptions=True)
        backend.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy", ["native", "python"])
async def test_coverage_unknowns_illegal_priority_and_typed_failure(strategy):
    from xreactor import IllegalBinError
    backend, signals = coverage_backend("native")
    schema = CoverGroupDef("state", (CoverPointDef("value", {
        "path": Bin.transition(1, 2), "low": Bin.range(0, 3),
        "masked": Bin.masked(4, 4, width=8),
        "ignored": Bin.ignore(Bin.values(9)), "bad": Bin.illegal(Bin.values(7)),
        "default": Bin.default(),
    }, overlap="allow"),))
    group = schema.instantiate("dut", illegal_policy="record").bind(
        trigger=RisingEdge(backend.clock), fields=signals, strategy=strategy)
    try:
        async with Execution(backend, coverage=[group]):
            for value in [1, "0b0000000x", 2, 9, 7, 16, 1, 2]:
                set_coverage_signal(signals["value"], value)
                await ClockCycles(backend.clock, 1)
            with pytest.raises(RuntimeError, match="owning Execution"):
                backend.close()
        report = group.report()
        assert report["points"][0]["counts"] == {
            "path": 1, "low": 4, "masked": 0, "ignored": 1, "bad": 1, "default": 1}
        assert report["points"][0]["unknown"] == 1
        group.illegal_policy = type(group.illegal_policy).RAISE
        set_coverage_signal(signals["value"], 7)
        with pytest.raises(IllegalBinError):
            async with Execution(backend, coverage=[group]):
                await ClockCycles(backend.clock, 3)
        assert group.samples == 1
        assert group.report()["points"][0]["counts"]["bad"] == 1
        assert backend.watcher_count == 0 and backend._owner is None
    finally:
        backend.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy", ["native", "python"])
async def test_coverage_drive_stable_and_data_isolation(strategy):
    from xreactor import DriveStable, CoverageMergeError, CoverGroup
    backend, signals = coverage_backend("native")
    schema = CoverGroupDef("state", (CoverPointDef("value", {"one": Bin.values(1)}),))
    left = schema.instantiate("dut").bind(trigger=DriveStable(backend.clock), fields=signals, strategy=strategy)
    right = schema.instantiate("other").bind(trigger=DriveStable(backend.clock), fields=signals, strategy=strategy)
    try:
        async with Execution(backend, coverage=[left, right]):
            set_coverage_signal(signals["value"], 1)
            await ClockCycles(backend.clock, 3)
            left.reset()
            await ClockCycles(backend.clock, 2)
        assert left.samples == 2 and right.samples == 5
        before = left.report()
        restored = CoverGroup.from_report(before)
        assert restored.report() == before
        with pytest.raises(CoverageMergeError, match="duplicate or overlapping"):
            restored.merge(left)
        assert restored.samples == 2
        right.bind(trigger=RisingEdge(backend.clock), fields=signals, strategy=strategy)
        async with Execution(backend, coverage=[right]):
            await ClockCycles(backend.clock, 1)
        with pytest.raises(CoverageMergeError, match="contract"):
            restored.merge(right, require_instance=False)
        assert backend.watcher_count == 0
    finally:
        backend.close()


@xtrigger(sample=RisingEdge("clock"), mode="each_sample")
def zero_coverage_pattern(dut):
    return dut.value == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy", ["native", "python"])
async def test_unknown_pattern_condition_and_coverage_contract(strategy):
    backend, signals = coverage_backend("native")
    dut = SimpleNamespace(clock=backend.clock, **signals)
    schema = CoverGroupDef("pattern", (CoverPointDef("other", {"zero": Bin.values(0)}),))
    group = schema.instantiate("dut").bind(trigger=zero_coverage_pattern(dut), fields=signals, strategy=strategy)
    try:
        async with Execution(backend, coverage=[group]):
            signals["value"].Set("0b0000000x")
            await ClockCycles(backend.clock, 3)
            signals["value"].Set(0)
            await ClockCycles(backend.clock, 2)
        assert group.samples == 2
        group.bind(trigger=RisingEdge(backend.clock), fields=signals, strategy=strategy, accumulate=True)
        with pytest.raises(ValueError, match="different sampling contract"):
            async with Execution(backend, coverage=[group]):
                pass
        assert group.samples == 2 and backend.watcher_count == 0
    finally:
        backend.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("width", [65, 128, 257])
@pytest.mark.parametrize("strategy", ["native", "python"])
async def test_wide_coverage_bins_snapshots_and_execution_isolation(width, strategy):
    """High bits participate in matching, diagnostics and unknown detection."""
    from xreactor import CoverGroup
    x = pytest.importorskip("xspcomm")
    clock = x.XClock(lambda _: 0)
    backend = XCommClockBackend(clock)
    value, gate = (x.XData(width, x.XData.InOut) for _ in range(2))
    high = 1 << (width - 1)
    schema = CoverGroupDef("wide", (
        CoverPointDef("value", {
            "exact": Bin.values(high + 1),
            "range": Bin.range(high, high + 2),
            "mask": Bin.masked(high + 1, high + 3, width=width),
            "path": Bin.transition(high + 1, high + 2),
            "ignore": Bin.ignore(Bin.values(high + 9)),
            "bad": Bin.illegal(Bin.values(high + 7)),
            "rest": Bin.default(),
        }, overlap="allow"),
        CoverPointDef("gate", {"enabled": Bin.values(high)}),
    ), (CrossDef("both", ("value", "gate")),), iff=Iff.equals("gate", high))
    group = schema.instantiate("dut", illegal_policy="record").bind(
        trigger=RisingEdge(clock), fields={"value": value, "gate": gate}, strategy=strategy)
    try:
        gate.Set(hex(high))
        async with Execution(backend, coverage=[group]):
            for data in [high + 1, high + 2, 1, high + 9, high + 7,
                         high + 1, "0bx" + "0" * (width - 1), high + 2,
                         "0bz" + "0" * (width - 1)]:
                value.Set(hex(data) if isinstance(data, int) else data)
                await ClockCycles(clock, 1)
            # The recorded illegal value must survive subsequent pin changes.
            value.Set("0x0")
            report = group.report()
            assert group.report() == report
            assert report["sampling_backend"] == strategy
            assert report["samples"] == 9
            assert report["points"][0]["counts"] == {
                "exact": 2, "range": 4, "mask": 2, "path": 1,
                "ignore": 1, "bad": 1, "rest": 1}
            assert report["points"][0]["unknown"] == 2
            assert report["crosses"][0]["counts"]['["path","enabled"]'] == 1
            assert group.illegal_hits[0].value == high + 7
            import json
            restored = CoverGroup.from_report(json.loads(json.dumps(report))).report()
            assert restored["illegal_hits"] == report["illegal_hits"]
            assert sorted(restored["points"], key=lambda p: p["name"]) == sorted(
                report["points"], key=lambda p: p["name"])
            gate.Set("0x0")
            await ClockCycles(clock, 1)
            assert group.report()["gated"] == 1
            group.reset()
            assert not group.illegal_hits
            gate.Set(hex(high))
            value.Set(hex(high + 1))
            await ClockCycles(clock, 1)
        group.bind(trigger=RisingEdge(clock), fields={"value": value, "gate": gate},
                   strategy=strategy, accumulate=True)
        value.Set(hex(high + 2))
        async with Execution(backend, coverage=[group]):
            await ClockCycles(clock, 1)
        assert group.samples == 2
        assert group.report()["points"][0]["counts"]["path"] == 0
        assert backend.watcher_count == 0 and backend._owner is None
        assert not backend._coverage_collectors
    finally:
        backend.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("width", [0, 64, 65])
@pytest.mark.parametrize("strategy", ["auto", "python"])
async def test_coverage_constants_outside_signal_width_are_not_truncated(width, strategy):
    x = pytest.importorskip("xspcomm")
    clock = x.XClock(lambda _: 0)
    backend = XCommClockBackend(clock)
    signal = x.XData(width, x.XData.InOut)
    bits = width or 1
    limit = 1 << bits
    group = CoverGroupDef("bounds", (CoverPointDef("value", {
        "maximum": Bin.values(limit - 1),
        "unreachable": Bin.values(limit + 1),
        "range": Bin.range(limit - 1, limit + 123),
        "outside_zero": Bin.masked(0, limit, width=bits + 1),
        "outside_one": Bin.masked(limit, limit, width=bits + 1),
        "low_bit": Bin.masked(1, 1, width=1),
        "path": Bin.transition(limit + 1, limit - 1),
    }, overlap="allow"),), iff=Iff.not_equals("value", limit + 1)).instantiate("dut").bind(
        trigger=RisingEdge(clock), fields={"value": signal}, strategy=strategy)
    try:
        async with Execution(backend, coverage=[group]):
            for value in (1, limit - 1):
                signal.Set(value if width == 0 else hex(value))
                await ClockCycles(clock, 1)
            signal.Set("X" if width == 0 else "0bx" + "0" * (width - 1))
            await ClockCycles(clock, 1)
        report = group.report()
        assert report["gated"] == 1  # folding an impossible Iff must still reject unknowns
        assert report["sampling_backend"] == ("native" if strategy == "auto" else "python")
        assert report["points"][0]["counts"] == {
            "maximum": 2 if bits == 1 else 1, "unreachable": 0,
            "range": 2 if bits == 1 else 1, "outside_zero": 2,
            "outside_one": 0, "low_bit": 2, "path": 0}
        assert backend.watcher_count == 0
    finally:
        backend.close()


@xtrigger(sample=RisingEdge("clock"))
def wide_coverage_pattern(dut):
    return Sequence(Wait(dut.value == (1 << 100) + 1), Next(dut.value == (1 << 100) + 2))


@xtrigger(sample=RisingEdge("clock"))
def overlapping_sequence(dut):
    return Sequence(Wait(dut.start), Within(1, 3, dut.done))


@xtrigger(sample=RisingEdge("clock"))
def overlapping_fsm(dut):
    return FSM(start="idle", states={
        "idle": State().when(dut.start).goto("busy"),
        "busy": State().when(dut.done).trigger("COMPLETE"),
    })


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy", ["native", "python"])
@pytest.mark.parametrize("pattern", [overlapping_sequence, overlapping_fsm])
@pytest.mark.parametrize("overlap", [False, True])
async def test_optional_pattern_overlap_and_summary(strategy, pattern, overlap):
    backend, fields = coverage_backend("native", names=("start", "done", "value"))
    dut = SimpleNamespace(clock=backend.clock, **fields)
    group = CoverGroupDef("parallel_matches", (
        CoverPointDef("value", {"zero": Bin.values(0)}),
    )).instantiate("dut").bind(trigger=pattern(dut), fields=fields, strategy=strategy,
                              overlap=overlap, max_active=2 if overlap else None,
                              diagnostics="summary")
    try:
        async with Execution(backend, coverage=[group]):
            for start, done in [(1, 0), (1, 0)]:
                fields["start"].Set(start)
                fields["done"].Set(done)
                await ClockCycles(backend.clock, 1)
            progress = group.inspect()
            assert len(progress["trigger"]) == (2 if overlap else 1)
            assert group.inspect() == progress
            for start, done in [(1, 1), (0, 1)]:
                fields["start"].Set(start)
                fields["done"].Set(done)
                await ClockCycles(backend.clock, 1)
        assert group.samples == (3 if overlap else 1)
        diagnostics = group.report()["diagnostics"]
        assert diagnostics["collected_runs"] == 1
        summary = diagnostics["patterns"][0]
        assert summary["started"] == summary["completed"] == group.samples
        assert summary["peak_active"] == (2 if overlap else 1)
        assert summary["unfinished_at_close"] == 0
        assert backend.watcher_count == 0 and group._collector is None
        with pytest.raises(RuntimeError, match="active Execution"):
            group.inspect()
        group.bind(trigger=pattern(dut), fields=fields, strategy=strategy,
                   overlap=overlap, max_active=2 if overlap else None)  # diagnostics off
        async with Execution(backend, coverage=[group]):
            for start, done in [(1, 0), (1, 0), (1, 1), (0, 1)]:
                fields["start"].Set(start)
                fields["done"].Set(done)
                await ClockCycles(backend.clock, 1)
        assert group.samples == (3 if overlap else 1)
        assert group.report()["diagnostics"] == {
            "collected_runs": 0, "uncollected_runs": 1, "patterns": []}
    finally:
        backend.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy", ["native", "python"])
async def test_wide_coverage_pattern_abort_and_illegal_failure(strategy):
    from xreactor import IllegalBinError
    x = pytest.importorskip("xspcomm")
    clock = x.XClock(lambda _: 0)
    backend = XCommClockBackend(clock)
    value, abort = x.XData(128, x.XData.InOut), x.XData(129, x.XData.InOut)
    dut = SimpleNamespace(clock=clock, value=value)
    high = 1 << 100
    group = CoverGroupDef("pattern", (CoverPointDef("value", {
        "other": Bin.values(high + 3),
        "bad": Bin.illegal(Bin.values(high + 2)),
    }),)).instantiate("dut", illegal_policy="record").bind(
        trigger=wide_coverage_pattern(dut), fields={"value": value, "abort": abort},
        abort=abort, strategy=strategy)
    try:
        async with Execution(backend, coverage=[group]):
            for number, cancel in [(1, 0), (2, 1 << 128), (2, 0), (1, 0), (2, 0)]:
                value.Set(hex(high + number))
                abort.Set(hex(cancel))
                await ClockCycles(clock, 1)
        assert group.samples == 1
        assert group.illegal_hits[0].value == high + 2
        group.illegal_policy = type(group.illegal_policy).RAISE
        with pytest.raises(IllegalBinError):
            async with Execution(backend, coverage=[group]):
                value.Set(hex(high + 1))
                await ClockCycles(clock, 1)
                value.Set(hex(high + 2))
                await ClockCycles(clock, 1)
        assert group.samples == 1
        assert group.illegal_hits[0].value == high + 2
        assert backend.watcher_count == 0 and backend._owner is None
        assert group._collector is None
    finally:
        backend.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy", ["native", "auto"])
async def test_coverage_old_abi_is_rejected_or_falls_back_before_sampling(monkeypatch, strategy):
    backend, fields = coverage_backend("native")
    monkeypatch.setattr(backend._engine, "CoverageVersion", lambda: 1)
    group = CoverGroupDef("abi", (CoverPointDef("value", {"zero": Bin.values(0)}),))
    group = group.instantiate("dut").bind(trigger=RisingEdge(backend.clock), fields=fields,
                                         strategy=strategy)
    try:
        if strategy == "native":
            with pytest.raises(NotImplementedError, match="descriptor version 5"):
                async with Execution(backend, coverage=[group]):
                    pass
            assert group.samples == 0
        else:
            async with Execution(backend, coverage=[group]):
                await ClockCycles(backend.clock, 2)
            report = group.report()
            assert report["sampling_backend"] == "python"
            assert "descriptor version 5" in report["sampling_fallback"]
            assert report["points"][0]["counts"]["zero"] == 2
        assert backend.watcher_count == 0 and backend._owner is None
        assert not backend._coverage_collectors
    finally:
        backend.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy", ["native", "python"])
@pytest.mark.parametrize("diagnostics", ["off", "summary"])
async def test_overlap_capacity_failure_keeps_evidence_and_releases_owner(strategy, diagnostics):
    backend, fields = coverage_backend("native", names=("start", "done", "value"))
    dut = SimpleNamespace(clock=backend.clock, **fields)
    group = CoverGroupDef("capacity", (CoverPointDef("value", {"zero": Bin.values(0)}),))
    group = group.instantiate("dut").bind(trigger=overlapping_sequence(dut), fields=fields,
        strategy=strategy, overlap=True, max_active=1, diagnostics=diagnostics)
    try:
        with pytest.raises(RuntimeError, match="active pattern capacity exhausted"):
            async with Execution(backend, coverage=[group]):
                fields["start"].Set(1)
                await ClockCycles(backend.clock, 2)
        report = group.report()
        assert report["samples"] == 0
        assert report["collection_complete"] is False
        with pytest.raises(AssertionError, match="incomplete"):
            group.assert_coverage(0)
        if diagnostics == "summary":
            summary = report["diagnostics"]["patterns"][0]
            assert summary["started"] == summary["unfinished_at_close"] == 1
        else:
            assert report["diagnostics"]["patterns"] == []
        assert backend.watcher_count == 0 and backend._owner is None
        assert not backend._coverage_collectors
        # Default isolation restores a usable group after a failed run.
        fields["start"].Set(0)
        async with Execution(backend, coverage=[group]):
            await ClockCycles(backend.clock, 1)
        assert group.report()["collection_complete"]
    finally:
        backend.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,strategy", [("native", "native"), ("native", "python"), ("memory", "python")])
async def test_pattern_diagnostics_deadline_abort_clear_reset_and_exit(kind, strategy):
    backend, fields = coverage_backend(kind, names=("start", "done", "value", "abort"))
    dut = SimpleNamespace(clock=backend.clock, **fields)
    group = CoverGroupDef("summary", (CoverPointDef("value", {"zero": Bin.values(0)}),))
    group = group.instantiate("dut").bind(trigger=overlapping_sequence(dut), fields=fields,
        abort=fields["abort"], strategy=strategy, overlap=True, max_active=2, diagnostics="summary")
    async def sample(start=0, done=0, abort=0):
        for key, value in (("start", start), ("done", done), ("abort", abort)):
            set_coverage_signal(fields[key], value)
        await ClockCycles(backend.clock, 1)
    try:
        async with Execution(backend, coverage=[group]):
            await sample(start=1)
            await sample()
            await sample()
            await sample(done=1)  # inclusive upper deadline
            await sample(start=1)
            for _ in range(4):
                await sample()  # expiration only after the deadline
            await sample(start=1)
            await sample(done=1, abort=1)  # abort wins over a completion
            await sample(start=1)
            group.clear_history()
            assert group.inspect()["trigger"] == []
            await sample(start=1)
            assert len(group.inspect()["trigger"]) == 1
        summary = group.report()["diagnostics"]["patterns"][0]
        assert {name: summary[name] for name in ("started", "completed", "expired", "aborted",
                                                "cleared", "unfinished_at_close", "peak_active")} == {
            "started": 5, "completed": 1, "expired": 1, "aborted": 1,
            "cleared": 1, "unfinished_at_close": 1, "peak_active": 1}
        assert summary["failed"] == 0
        assert group.samples == 1
        group.bind(trigger=overlapping_sequence(dut), fields=fields, abort=fields["abort"],
                   strategy=strategy, overlap=True, max_active=2, diagnostics="summary", accumulate=True)
        async with Execution(backend, coverage=[group]):
            await sample(done=1)  # previous partial match cannot complete here
            assert group.samples == 1
            group.reset()
            assert group.samples == 0
            assert group.report()["diagnostics"]["patterns"][0]["started"] == 0
            await sample(start=1)
            await sample(done=1)
        assert group.samples == 1
        assert group.report()["diagnostics"]["patterns"][0]["unfinished_at_close"] == 0
        assert backend.watcher_count == 0
    finally:
        backend.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy", ["native", "python"])
async def test_transition_summary_and_mixed_diagnostic_runs(strategy):
    from xreactor import CoverGroup, CoverageMergeError
    backend, fields = coverage_backend("native")
    group = CoverGroupDef("bin_summary", (CoverPointDef("value", {
        "path": Bin.transition(1, 2),
    }, iff=Iff.equals("gate", 1)),)).instantiate("dut")
    binding = dict(trigger=RisingEdge(backend.clock), fields=fields, abort=fields["abort"], strategy=strategy)
    group.bind(**binding, diagnostics="summary")
    try:
        async with Execution(backend, coverage=[group]):
            for value, gate, abort in [(1, 1, 0), (9, 1, 0), (1, 1, 0), (2, 1, 0),
                                      (1, 1, 0), (2, 1, 1), (1, 1, 0), (2, 0, 0),
                                      (1, 1, 0)]:
                for name, number in (("value", value), ("gate", gate), ("abort", abort)):
                    fields[name].Set(number)
                await ClockCycles(backend.clock, 1)
            assert len(group.inspect()["bins"]["value"]["path"]) == 1
        report = group.report()
        summary = report["diagnostics"]["patterns"][0]
        assert {name: summary[name] for name in ("started", "completed", "failed", "aborted", "cleared",
                                                "unfinished_at_close")} == {
            "started": 5, "completed": 1, "failed": 1, "aborted": 1, "cleared": 1, "unfinished_at_close": 1}
        group.bind(**binding, diagnostics="off", accumulate=True)
        async with Execution(backend, coverage=[group]):
            fields["value"].Set(1)
            await ClockCycles(backend.clock, 1)
            assert len(group.inspect()["bins"]["value"]["path"]) == 1
            fields["value"].Set(2)
            await ClockCycles(backend.clock, 1)
        mixed = group.report()
        assert mixed["points"][0]["counts"]["path"] == 2
        assert mixed["diagnostics"]["collected_runs"] == mixed["diagnostics"]["uncollected_runs"] == 1
        assert mixed["diagnostics"]["patterns"] == report["diagnostics"]["patterns"]
        restored = CoverGroup.from_report(mixed)
        assert restored.report() == mixed
        with pytest.raises(CoverageMergeError, match="duplicate or overlapping"):
            restored.merge(CoverGroup.from_report(report))
        combined = restored.report()["diagnostics"]
        assert combined["collected_runs"] == 1 and combined["uncollected_runs"] == 1
        assert combined["patterns"][0]["completed"] == 1
        assert combined["patterns"][0]["peak_active"] == 1
    finally:
        backend.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy", ["native", "python"])
@pytest.mark.parametrize("exit_kind", ["exception", "cancel"])
async def test_pending_pattern_diagnostics_survive_abnormal_exit(strategy, exit_kind):
    import asyncio
    backend, fields = coverage_backend("native", names=("start", "done", "value"))
    dut = SimpleNamespace(clock=backend.clock, **fields)
    group = CoverGroupDef("pending", (CoverPointDef("value", {"zero": Bin.values(0)}),))
    group = group.instantiate("dut").bind(trigger=overlapping_fsm(dut), fields=fields,
        overlap=True, max_active=2, diagnostics="summary", strategy=strategy)
    started = asyncio.Event()
    outside = asyncio.create_task(asyncio.Event().wait())
    original = ValueError("case failed")
    async def simulation():
        async with Execution(backend, coverage=[group]):
            fields["start"].Set(1)
            await ClockCycles(backend.clock, 1)
            started.set()
            if exit_kind == "exception":
                raise original
            await asyncio.Event().wait()
    task = asyncio.create_task(simulation())
    try:
        await started.wait()
        if exit_kind == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(ValueError) as error:
                await task
            assert error.value is original
        summary = group.report()["diagnostics"]["patterns"][0]
        assert summary["started"] == summary["unfinished_at_close"] == 1
        assert summary["aborted"] == summary["cleared"] == 0
        assert not outside.done()
        assert backend.watcher_count == 0 and backend._owner is None
        assert group._collector is None and not group._history
    finally:
        outside.cancel()
        await asyncio.gather(outside, return_exceptions=True)
        backend.close()


def test_overlap_configuration_is_explicit_and_fixed_before_execution():
    backend, fields = coverage_backend("memory", names=("start", "done", "value"))
    dut = SimpleNamespace(clock=backend.clock, **fields)
    group = CoverGroupDef("config", (CoverPointDef("value", {"zero": Bin.values(0)}),)).instantiate("dut")
    for options in ({"overlap": True}, {"overlap": True, "max_active": 0},
                    {"max_active": 2}, {"overlap": True, "max_active": True},
                    {"diagnostics": "trace"}):
        with pytest.raises(ValueError):
            group.bind(trigger=overlapping_sequence(dut), fields=fields, **options)
    with pytest.raises(ValueError, match="compiled Sequence or FSM"):
        group.bind(trigger=RisingEdge(backend.clock), fields=fields, overlap=True, max_active=2)
    backend.close()


@xtrigger(sample=RisingEdge("clock"))
def branching_attempts(dut):
    return FSM(start="idle", states={
        "slow": State().when(dut.value == 2).goto("waiting").when(dut.value == 4).goto("idle"),
        "idle": State().when(dut.value == 1).goto("slow").when(dut.value == 2).goto("fast")
                       .when(dut.value == 3).trigger("DIRECT").otherwise().goto("idle"),
        "fast": State().when(dut.value == 3).trigger("FAST"),
        "waiting": State().when(dut.value == 3).trigger("SLOW"),
    })


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy", ["native", "python"])
async def test_overlapping_fsm_independent_branches_start_self_loop_and_return(strategy):
    backend, fields = coverage_backend("native", names=("value",))
    dut = SimpleNamespace(clock=backend.clock, **fields)
    group = CoverGroupDef("branches", (CoverPointDef("value", {"three": Bin.values(3)}),))
    group = group.instantiate("dut").bind(trigger=branching_attempts(dut), fields=fields,
        strategy=strategy, overlap=True, max_active=2, diagnostics="summary")
    try:
        async with Execution(backend, coverage=[group]):
            for value, states in [(0, []), (1, ["slow"]), (2, ["waiting", "fast"]),
                                  (3, []), (1, ["slow"]), (4, [])]:
                fields["value"].Set(value)
                await ClockCycles(backend.clock, 1)
                assert group.inspect()["trigger"] == [{"state": name} for name in states]
        # Two existing branches and an immediate start-state terminal complete together.
        assert group.samples == group.report()["points"][0]["counts"]["three"] == 3
        summary = group.report()["diagnostics"]["patterns"][0]
        assert summary["started"] == 4
        assert summary["completed"] == 3
        assert summary["failed"] == 1  # return to start, without a terminal
        assert summary["peak_active"] == 2
        assert summary["unfinished_at_close"] == 0
        assert backend.watcher_count == 0
    finally:
        backend.close()
