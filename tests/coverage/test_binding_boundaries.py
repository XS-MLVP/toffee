"""Clock-bound coverage validates its contract and releases failed registrations."""

from enum import IntEnum
from types import SimpleNamespace

import pytest

from toffee import (
    Bin, ClockCycles, CoverGroupDef, CoverPointDef, Execution, MemoryBackend,
    Next, RisingEdge, Sequence, Wait, XCommClockBackend, pytrigger,
)
from toffee.ir import ConstantExpr, signal_expr
from toffee.triggers import CompiledTrigger


def group():
    return CoverGroupDef("bound", (CoverPointDef("value", {"zero": Bin.values(0)}),)).instantiate("dut")


@pytest.mark.parametrize("options, message", [
    ({"trigger": object()}, "coverage trigger"),
    ({"strategy": "fast"}, "strategy"),
    ({"accumulate": 1}, "accumulate"),
    ({"contract": ""}, "contract"),
    ({"fields": {}}, "fields"),
    ({"fields": {1: object()}}, "fields"),
    ({"overlap": 1}, "overlap"),
    ({"diagnostics": "all"}, "diagnostics"),
    ({"max_active": True}, "uint32"),
    ({"max_active": 1 << 32}, "uint32"),
    ({"overlap": True, "max_active": 2}, "compiled Sequence"),
    ({"max_active": 2}, "overlap=True"),
])
def test_invalid_binding_options_are_rejected(options, message):
    args = dict(trigger=RisingEdge(object()), fields={"value": object()})
    args.update(options)
    with pytest.raises((ValueError, TypeError), match=message):
        group().bind(**args)


def test_overlap_requires_bounded_attempts_and_initial_wait():
    for program, options, message in [
        (Sequence(Wait(True)), {"overlap": True}, "explicit max_active"),
        (Sequence(Next(True)), {"overlap": True, "max_active": 2}, "start with Wait"),
    ]:
        trigger = CompiledTrigger("pattern", None, program, RisingEdge(object()))
        with pytest.raises(ValueError, match=message):
            group().bind(trigger=trigger, fields={"value": object()}, **options)


@pytest.mark.asyncio
@pytest.mark.parametrize("problem,message", [
    ("phase", "explicit sampling phase"),
    ("clock", "sampling clock"),
    ("abort", "abort must be a named"),
    ("field", "unbound coverage fields"),
    ("manual", "manual samples"),
    ("bound-expression", "bound pattern expressions"),
    ("nonserializable", "explicit contract"),
])
async def test_failed_coverage_startup_releases_execution(problem, message):
    backend = MemoryBackend(object())
    signal = SimpleNamespace(value=0, width=8)
    trigger = RisingEdge(backend.clock)
    options = {}
    fields = {"value": signal}
    candidate = group()
    if problem == "phase":
        trigger = CompiledTrigger("predicate", None, ConstantExpr(True))
    elif problem == "clock":
        trigger = RisingEdge(object())
    elif problem == "abort":
        options["abort"] = SimpleNamespace(value=0)
    elif problem == "field":
        fields = {"other": signal}
    elif problem == "manual":
        candidate.sample({"value": 0})
        options["accumulate"] = True
    else:
        program = signal_expr(signal) if problem == "bound-expression" else ConstantExpr(object())
        trigger = CompiledTrigger("predicate", None, program, trigger)
    candidate.bind(trigger=trigger, fields=fields, **options)
    with pytest.raises(ValueError, match=message):
        async with Execution(backend, coverage=[candidate]):
            pytest.fail("invalid coverage must fail before entering the body")
    assert backend._owner is None and backend.watcher_count == 0
    async with Execution(backend):
        assert (await ClockCycles(backend.clock, 1)).tick == 2


@pytest.mark.asyncio
async def test_default_phase_and_live_report_thread_ownership():
    import asyncio

    backend = MemoryBackend(object())
    trigger = CompiledTrigger("always", None, ConstantExpr(True))
    candidate = group().bind(trigger=trigger, fields={"value": SimpleNamespace(value=0)})
    async with Execution(backend, default_sample=RisingEdge(backend.clock), coverage=[candidate]):
        await ClockCycles(backend.clock, 1)
        with pytest.raises(RuntimeError, match="Execution thread"):
            await asyncio.to_thread(candidate.report)
        with pytest.raises(RuntimeError, match="owning Execution"):
            backend.close()
        candidate.assert_coverage()
    assert candidate.report()["samples"] == 1


@pytest.mark.asyncio
async def test_live_coverage_cannot_be_rebound_merged_or_shared():
    backend = MemoryBackend(object())
    candidate = group().bind(trigger=RisingEdge(backend.clock), fields={"value": SimpleNamespace(value=0)})
    with pytest.raises(ValueError, match="distinct instances"):
        Execution(backend, coverage=[candidate, candidate])
    with pytest.raises(RuntimeError, match="bound before starting"):
        async with Execution(backend, coverage=[group()]):
            pytest.fail("manual group must be bound")
    async with Execution(backend, coverage=[candidate]):
        with pytest.raises(RuntimeError, match="rebind active"):
            candidate.bind(trigger=RisingEdge(backend.clock), fields={"value": object()})
        with pytest.raises(RuntimeError, match="already belongs"):
            async with Execution(MemoryBackend(object()), coverage=[candidate]):
                pytest.fail("a live group cannot belong to two Executions")
        from toffee import CoverGroup, CoverageMergeError
        independent = CoverGroup.from_report(candidate.report())
        with pytest.raises(CoverageMergeError, match="active coverage"):
            candidate.merge(independent)
        await ClockCycles(backend.clock, 1)
        candidate.assert_coverage()


@pytest.mark.asyncio
async def test_python_sampling_resolves_nested_field_paths():
    backend = MemoryBackend(object())
    candidate = CoverGroupDef("nested", (
        CoverPointDef("code", {"zero": Bin.values(0)}, source="header.code"),
    )).instantiate("dut").bind(trigger=RisingEdge(backend.clock),
                              fields={"header.code": SimpleNamespace(value=0)}, strategy="python")
    async with Execution(backend, coverage=[candidate]):
        await ClockCycles(backend.clock, 1)
    candidate.assert_coverage()


@pytrigger(sample=RisingEdge("clock"))
def python_predicate(dut):
    return True


def test_python_predicate_requires_a_stable_contract():
    with pytest.raises(ValueError, match="stable contract"):
        group().bind(trigger=python_predicate(SimpleNamespace(clock=object())), fields={"value": object()})


@pytest.mark.asyncio
@pytest.mark.parametrize("problem", ["predicate", "field", "negative", "string"])
async def test_auto_strategy_falls_back_but_native_strategy_reports_unsupported_input(problem):
    xspcomm = pytest.importorskip("xspcomm")
    clock = xspcomm.XClock(lambda _: 0)
    backend = XCommClockBackend(clock)
    signal = xspcomm.XData(8, xspcomm.XData.InOut)
    signal.Set(0)
    trigger = python_predicate(SimpleNamespace(clock=clock)) if problem == "predicate" else RisingEdge(clock)
    if problem == "field":
        signal = SimpleNamespace(value=0, width=8)
    value = -1 if problem == "negative" else "zero" if problem == "string" else 0
    definition = CoverGroupDef("fallback", (CoverPointDef("value", {"bin": Bin.values(value)}),))
    try:
        for strategy in ("native", "auto"):
            candidate = definition.instantiate("dut").bind(
                trigger=trigger, fields={"value": signal}, strategy=strategy, contract="fallback/v1",
            )
            if strategy == "native":
                with pytest.raises(NotImplementedError):
                    async with Execution(backend, coverage=[candidate]):
                        pytest.fail("unsupported input cannot use native sampling")
                assert backend.watcher_count == 0 and backend._owner is None
            else:
                async with Execution(backend, coverage=[candidate]):
                    await ClockCycles(clock, 1)
                report = candidate.report()
                assert report["sampling_backend"] == "python" and report["sampling_fallback"]
                assert report["samples"] == 1
    finally:
        backend.close()


@pytest.mark.asyncio
async def test_native_enum_constants_and_failed_attachment_cleanup(monkeypatch):
    xspcomm = pytest.importorskip("xspcomm")

    class Code(IntEnum):
        ZERO = 0

    clock = xspcomm.XClock(lambda _: 0)
    backend = XCommClockBackend(clock)
    signal = xspcomm.XData(8, xspcomm.XData.InOut)
    signal.Set(0)
    candidate = CoverGroupDef("enum", (CoverPointDef("value", {"zero": Bin.values(Code.ZERO)}),)).instantiate("dut")
    candidate.bind(trigger=RisingEdge(clock), fields={"value": signal}, strategy="native")
    failure = RuntimeError("coverage attachment rejected")

    def broken(*args):
        raise failure

    try:
        with monkeypatch.context() as patch:
            patch.setattr(backend._engine, "AttachCoverage", broken)
            with pytest.raises(RuntimeError) as caught:
                async with Execution(backend, coverage=[candidate]):
                    pytest.fail("failed attachment cannot enter the body")
            assert caught.value is failure
        assert backend.watcher_count == 0 and backend._owner is None
        async with Execution(backend, coverage=[candidate]):
            await ClockCycles(clock, 1)
            candidate.assert_coverage()
    finally:
        backend.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("problem,message", [
    ("generation", "generation/epoch"),
    ("counters", "cumulative snapshot"),
    ("illegal", "diagnostic snapshot"),
    ("diagnostics", "diagnostic counters"),
])
async def test_invalid_native_snapshots_are_sticky_and_do_not_add_hits(monkeypatch, problem, message):
    xspcomm = pytest.importorskip("xspcomm")
    clock = xspcomm.XClock(lambda _: 0)
    backend = XCommClockBackend(clock)
    signal = xspcomm.XData(8, xspcomm.XData.InOut)
    signal.Set(0)
    candidate = group().bind(trigger=RisingEdge(clock), fields={"value": signal},
                             strategy="native", diagnostics="summary")
    before = None
    try:
        with pytest.raises(RuntimeError, match=message):
            async with Execution(backend, coverage=[candidate]):
                await ClockCycles(clock, 1)
                before = candidate.report()
                snapshot = backend._engine.CoverageSnapshot

                def corrupted(handle):
                    original = snapshot(handle)
                    value = SimpleNamespace(**{name: getattr(original, name) for name in (
                        "generation", "epoch", "counters", "illegal_bins", "illegal_values", "illegal_ticks", "diagnostics",
                    )})
                    if problem == "generation":
                        value.generation += 1
                    elif problem == "counters":
                        value.counters = []
                    elif problem == "illegal":
                        value.illegal_ticks = [1]
                    else:
                        value.diagnostics = []
                    return value

                monkeypatch.setattr(backend._engine, "CoverageSnapshot", corrupted)
                with pytest.raises(RuntimeError, match=message) as first:
                    candidate.sync()
                with pytest.raises(RuntimeError) as second:
                    candidate.sync()
                assert second.value is first.value
        assert backend.watcher_count == 0 and backend._owner is None
        after = candidate.report()
        assert after["samples"] == before["samples"]
        assert after["points"][0]["counts"] == before["points"][0]["counts"]
    finally:
        backend.close()


@pytest.mark.asyncio
async def test_failure_after_registration_releases_python_sampling_handle(monkeypatch):
    xspcomm = pytest.importorskip("xspcomm")
    clock = xspcomm.XClock(lambda _: 0)
    backend = XCommClockBackend(clock)
    signal = xspcomm.XData(8, xspcomm.XData.InOut)
    candidate = group().bind(trigger=RisingEdge(clock), fields={"value": signal}, strategy="python")
    failure = OSError("reset interrupted")

    def broken():
        raise failure

    try:
        with monkeypatch.context() as patch:
            patch.setattr(candidate, "reset", broken)
            with pytest.raises(OSError) as caught:
                async with Execution(backend, coverage=[candidate]):
                    pytest.fail("failed startup cannot enter the body")
            assert caught.value is failure
        assert backend.watcher_count == 0 and backend._owner is None
        async with Execution(backend, coverage=[candidate]):
            await ClockCycles(clock, 1)
            candidate.assert_coverage()
    finally:
        backend.close()


@pytest.mark.asyncio
async def test_gate_clears_temporal_history_and_keeps_pattern_diagnostics(tmp_path):
    from toffee import CoverageDatabase, Iff, LogicValue, generate_unified_coverage_site

    backend = MemoryBackend(object())
    value, enabled = SimpleNamespace(value=LogicValue(1, 0, 8)), SimpleNamespace(value=1)
    candidate = CoverGroupDef("gated-pattern", (CoverPointDef("value", {
        "one": Bin.values(1), "path": Bin.transition(1, 2),
    }),), iff=Iff.equals("enabled", 1)).instantiate("dut").bind(
        trigger=RisingEdge(backend.clock), fields={"value": value, "enabled": enabled},
        strategy="python", diagnostics="summary",
    )
    async with Execution(backend, coverage=[candidate]):
        await ClockCycles(backend.clock, 1)
        enabled.value = 0
        await ClockCycles(backend.clock, 1)
        enabled.value = 1
        value.value = 2
        await ClockCycles(backend.clock, 1)
    report = candidate.report()
    assert report["points"][0]["counts"]["path"] == 0
    pattern, = report["diagnostics"]["patterns"]
    assert pattern["cleared"] == 1
    path = CoverageDatabase([candidate]).write_json(tmp_path / "functional.json")
    generate_unified_coverage_site(functional_paths=[path], output_dir=tmp_path / "site")
    pages = list((tmp_path / "site/functional/groups").glob("*/index.html"))
    assert len(pages) == 1 and "Pattern diagnostics" in pages[0].read_text()


@pytest.mark.asyncio
async def test_enum_pattern_shape_and_merge_of_python_and_native_evidence():
    from toffee import CoverGroup

    class Truth(IntEnum):
        TRUE = 1

    backend = MemoryBackend(object())
    candidate = group().bind(
        trigger=CompiledTrigger("enum", None, ConstantExpr(Truth.TRUE), RisingEdge(backend.clock)),
        fields={"value": SimpleNamespace(value=0)}, diagnostics="summary", accumulate=True,
    )
    async with Execution(backend, coverage=[candidate]):
        await ClockCycles(backend.clock, 1)
    first = candidate.report()
    async with Execution(backend, coverage=[candidate]):
        await ClockCycles(backend.clock, 1)
    assert candidate.samples == 2
    # Native and Python groups with the same stable contract may contribute
    # independent evidence to a combined report.
    xspcomm = pytest.importorskip("xspcomm")
    clock = xspcomm.XClock(lambda _: 0)
    native = XCommClockBackend(clock)
    signal = xspcomm.XData(8, xspcomm.XData.InOut)
    signal.Set(0)
    left, right = group(), group()
    memory = MemoryBackend(object())
    try:
        for engine, source, target, strategy in ((memory, SimpleNamespace(value=0, width=8), left, "python"),
                                                  (native, signal, right, "native")):
            target.bind(trigger=RisingEdge(engine.clock), fields={"value": source}, strategy=strategy)
            async with Execution(engine, coverage=[target]):
                await ClockCycles(engine.clock, 1)
        left.merge(right)
        assert left.report()["sampling_backend"] == "mixed" and left.samples == 2
        assert CoverGroup.from_report(first).report() == first
    finally:
        native.close()


@pytest.mark.asyncio
async def test_exhausted_pattern_capacity_is_marked_incomplete_in_html(tmp_path):
    from toffee import CoverageDatabase, Hold, generate_unified_coverage_site

    backend = MemoryBackend(object())
    pattern = CompiledTrigger("overlap", None, Sequence(Wait(True), Hold(True, cycles=4)), RisingEdge(backend.clock))
    candidate = group().bind(trigger=pattern, fields={"value": SimpleNamespace(value=0)},
                             strategy="python", overlap=True, max_active=1)
    with pytest.raises(RuntimeError, match="capacity exhausted"):
        async with Execution(backend, coverage=[candidate]):
            await ClockCycles(backend.clock, 3)
    assert not candidate.report()["collection_complete"] and not candidate.covered
    path = CoverageDatabase([candidate]).write_json(tmp_path / "functional.json")
    generate_unified_coverage_site(functional_paths=[path], output_dir=tmp_path / "site")
    page, = (tmp_path / "site/functional/groups").glob("*/index.html")
    assert "Coverage collection is incomplete" in page.read_text()
