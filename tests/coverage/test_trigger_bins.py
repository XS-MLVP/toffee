from __future__ import annotations

import asyncio

import pytest

from xreactor import (
    ClockCycles, ConditionMode, Execution, FallingEdge, FSM, RisingEdge, Sequence, State,
    Wait, Within, xtrigger, CoverGroup as CoreGroup, CoverGroupDef,
    CoverPointDef, Iff, CrossDef,
)
from xreactor.coverage import PatternMatcher, BinSpec
from xreactor.declarative import (
    Bin, DefinitionError, SignalCoverGroup, TemporalCoverPoint, covergroup,
)
from xreactor.ir import signal_expr, program_to_dict
from examples.coverage.declarative_protocol import ProtocolBundle, ToyResponder
from examples.coverage.trigger_bins import ProtocolCoverage, Completions, checked_tag_one, roundtrip, run

MODES = [("memory", "python"), ("xcomm", "python"), ("xcomm", "native")]


def point(report, name):
    return next(p for p in report["points"] if p["name"] == name)


@pytest.mark.parametrize("engine,strategy", MODES)
def test_full_programs_run_independently(engine, strategy):
    report = asyncio.run(run(engine=engine, strategy=strategy))
    assert report["samples"] == 12
    assert point(report, "requests")["counts"] == {"handshake": 3}
    assert point(report, "completions")["counts"] == {
        "tag_one": 1, "tag_one_fast": 0, "tag_two_ready": 1, "correct_data": 1, "bad_data": 0,
    }
    assert report["sampling_backend"] == strategy
    restored = CoreGroup.from_report(report)
    assert restored.definition.to_dict()["version"] == 3
    assert restored.report()["points"] == report["points"]


@pytest.mark.parametrize("engine,strategy", MODES)
def test_unselected_fsm_terminal_still_ends_attempt(engine, strategy):
    report = asyncio.run(run(engine=engine, strategy=strategy, wrong=(1,)))
    counts = point(report, "completions")["counts"]
    assert counts["bad_data"] == 1 and counts["correct_data"] == 0
    assert counts["tag_one"] == 1  # Illegal bin cannot suppress another program.
    assert len(report["illegal_hits"]) == 1


@xtrigger(sample=RisingEdge("clock"))
def delayed(pins: ProtocolBundle):
    return Sequence(Wait(signal_expr(pins.request.valid)), Within(1, 5, signal_expr(pins.response.valid)))


@xtrigger(sample=RisingEdge("clock"), mode=ConditionMode.EACH_SAMPLE)
def level(pins: ProtocolBundle):
    return signal_expr(pins.request.valid)


@xtrigger(sample=RisingEdge("clock"), mode=ConditionMode.CHANGE)
def change(pins: ProtocolBundle):
    return signal_expr(pins.request.valid)


@xtrigger(sample=RisingEdge("clock"))
def enter(pins: ProtocolBundle):
    return signal_expr(pins.request.valid)


class Events(TemporalCoverPoint[ProtocolBundle]):
    many = Bin.pattern(delayed, overlap=True, max_active=4)
    single = delayed
    high = level
    changed = change
    rising = enter


@covergroup(contract="test.trigger-bins/v1")
class EventCoverage(SignalCoverGroup[ProtocolBundle]):
    first = Events()
    second = Events()  # Same declarations, separate execution state and counters.


async def manual_run(engine, strategy, rows, *, group_type=EventCoverage, abort=False, reset_at=None, groups=None, diagnostics="off", on_start=None):
    toy = ToyResponder(engine=engine)
    if toy.native:
        toy.clock.RemoveStepRisCbByDesc("_evaluate_native")
    else:
        toy.backend.on_phase = None
    # Avoid the DUT substituting a response; the rows are the observed waveforms.
    group = group_type(instance="test")
    if groups is not None:
        groups.append(group)
    group.bind(pins=toy.pins, sample=RisingEdge(toy.clock), strategy=strategy,
               abort=toy.pins.response.ready if abort else None, diagnostics=diagnostics)
    try:
        async with Execution(toy.backend, coverage=[group.runtime]):
            if on_start is not None:
                on_start(group, toy)
            for index, row in enumerate(rows):
                if index == reset_at:
                    group.runtime.clear_history()
                toy.pins.request.valid.Set(row[0])
                toy.pins.response.valid.Set(row[1])
                toy.pins.response.ready.Set(row[2] if len(row) > 2 else 0)
                await ClockCycles(toy.clock, 1)
            report = group.report()
            progress = group.runtime.inspect()
        assert toy.backend.watcher_count == 0
        return report, progress
    finally:
        toy.close()


@pytest.mark.parametrize("engine,strategy", MODES)
def test_overlapping_completion_count_and_expression_modes(engine, strategy):
    report, progress = asyncio.run(manual_run(engine, strategy, [(1, 0), (1, 0), (1, 0), (0, 1), (0, 0)]))
    expected = {"many": 3, "single": 1, "high": 3, "changed": 2, "rising": 1}
    assert point(report, "first")["counts"] == point(report, "second")["counts"] == expected
    assert progress["bins"] == {}


@pytest.mark.parametrize("engine,strategy", MODES)
@pytest.mark.parametrize("reset", [False, True])
def test_abort_and_clear_discard_pending_attempts(engine, strategy, reset):
    rows = [(1, 0, 0), (0, 0, int(not reset)), (0, 1, 0)]
    report, _ = asyncio.run(manual_run(engine, strategy, rows, abort=not reset, reset_at=1 if reset else None))
    assert point(report, "first")["counts"]["many"] == 0
    assert point(report, "first")["counts"]["single"] == 0


def test_definition_checks_and_frozen_specialization():
    assert program_to_dict(roundtrip.with_args(tag=2, maximum=3).program) != program_to_dict(roundtrip.program)
    toy = ToyResponder(engine="memory")
    try:
        fixed = roundtrip.with_args(tag=2, maximum=3)
        assert fixed(toy.pins).program is fixed.program
        with pytest.raises(ValueError, match="phase conflicts"):
            fixed.bind(toy.pins, sample=FallingEdge(toy.clock))
        group = ProtocolCoverage(instance="test")
        with pytest.raises(TypeError, match="Execution"):
            group.sample(toy.pins)
        with pytest.raises(DefinitionError, match="E_CAPACITY"):
            Bin.pattern(roundtrip, overlap=True)
        with pytest.raises(DefinitionError, match="E_TERMINAL"):
            Bin.pattern(checked_tag_one)
        with pytest.raises(TypeError, match="immutable"):
            roundtrip.with_args(tag=[1])
    finally:
        toy.close()


def test_no_cross_between_pattern_points():
    schema = ProtocolCoverage.compile().schema
    with pytest.raises(ValueError, match="pattern"):
        CoverGroupDef("bad", schema.points, (CrossDef("cross", ("requests", "completions")),))


@pytest.mark.parametrize("engine,strategy", MODES)
def test_point_gate_clears_patterns_and_expression_history(engine, strategy):
    toy = ToyResponder(engine=engine)
    if toy.native:
        toy.clock.RemoveStepRisCbByDesc("_evaluate_native")
    else:
        toy.backend.on_phase = None
    bins = {"path": BinSpec(PatternMatcher(delayed.program)),
            "edge": BinSpec(PatternMatcher(enter.program))}
    group = CoverGroupDef("gate", (CoverPointDef("events", bins, iff=Iff("gate", (1,))),)).instantiate("test")
    group.bind(trigger=RisingEdge(toy.clock), root=toy.pins, strategy=strategy,
               fields={"gate": toy.pins.request.ready, "request.valid": toy.pins.request.valid,
                       "response.valid": toy.pins.response.valid}, diagnostics="summary")
    try:
        async def execute():
            async with Execution(toy.backend, coverage=[group]):
                for request, response, gate in [(1, 0, 1), (1, 0, 0), (1, 1, 1), (0, 1, 1)]:
                    toy.pins.request.valid.Set(request)
                    toy.pins.response.valid.Set(response)
                    toy.pins.request.ready.Set(gate)
                    await ClockCycles(toy.clock, 1)
        asyncio.run(execute())
        report = group.report()
        assert point(report, "events")["counts"] == {"path": 1, "edge": 2}
        assert point(report, "events")["gated"] == 1
        path = next(p for p in report["diagnostics"]["patterns"] if p["bin"] == "path")
        assert path["cleared"] == path["completed"] == 1
    finally:
        toy.close()


class SmallCapacity(TemporalCoverPoint[ProtocolBundle]):
    path = Bin.pattern(delayed, overlap=True, max_active=1)


@covergroup(contract="test.small-capacity/v1")
class SmallCapacityGroup(SignalCoverGroup[ProtocolBundle]):
    events = SmallCapacity()


@pytest.mark.parametrize("engine,strategy", MODES)
def test_bin_capacity_failure_marks_collection_incomplete_and_cleans_up(engine, strategy):
    groups = []
    with pytest.raises((RuntimeError, OverflowError), match="capacity"):
        asyncio.run(manual_run(engine, strategy, [(1, 0), (1, 0)],
                               group_type=SmallCapacityGroup, groups=groups))
    assert groups[0].report()["collection_complete"] is False
    assert groups[0].runtime._collector is None


@pytest.mark.parametrize("engine,strategy", MODES)
def test_cancelled_ordinary_wait_does_not_reset_coverage_pattern(engine, strategy):
    toy = ToyResponder(engine=engine)
    if toy.native:
        toy.clock.RemoveStepRisCbByDesc("_evaluate_native")
    else:
        toy.backend.on_phase = None
    group = EventCoverage(instance="test").bind(pins=toy.pins, sample=RisingEdge(toy.clock), strategy=strategy)
    try:
        async def execute():
            async with Execution(toy.backend, coverage=[group.runtime]):
                async def waiter():
                    await delayed(toy.pins)
                task = asyncio.create_task(waiter())
                toy.pins.request.valid.Set(1)
                await ClockCycles(toy.clock, 1)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                toy.pins.request.valid.Set(0)
                toy.pins.response.valid.Set(1)
                await ClockCycles(toy.clock, 1)
        asyncio.run(execute())
        assert group.first.count(Events.many) == group.first.count(Events.single) == 1
        assert toy.backend.watcher_count == 0
    finally:
        toy.close()


@pytest.mark.parametrize("strategy", ["auto", "native"])
def test_old_native_abi_fallback_is_explicit(monkeypatch, strategy):
    toy = ToyResponder(engine="xcomm")
    monkeypatch.setattr(toy.backend._engine, "CoverageVersion", lambda: 3)
    group = ProtocolCoverage(instance="test").bind(pins=toy.pins, sample=RisingEdge(toy.clock), strategy=strategy)
    try:
        async def execute():
            async with Execution(toy.backend, coverage=[group.runtime]):
                await ClockCycles(toy.clock, 1)
        if strategy == "native":
            with pytest.raises(NotImplementedError, match="descriptor version 5"):
                asyncio.run(execute())
        else:
            asyncio.run(execute())
            report = group.report()
            assert report["sampling_backend"] == "python"
            assert "descriptor version 5" in report["sampling_fallback"]
        assert toy.backend.watcher_count == 0
    finally:
        toy.close()


def test_native_bins_count_without_python_sampling_or_per_hit_wakeups(monkeypatch):
    toy = ToyResponder(engine="xcomm")
    toy.clock.RemoveStepRisCbByDesc("_evaluate_native")
    toy.pins.request.valid.Set(1)
    group = EventCoverage(instance="test").bind(pins=toy.pins, sample=RisingEdge(toy.clock), strategy="native")
    calls = []
    original = toy.backend.run_until
    def run_until(limit):
        calls.append(limit)
        return original(limit)
    def forbidden(*args, **kwargs):
        raise AssertionError("native coverage called the Python sample path")
    monkeypatch.setattr(toy.backend, "run_until", run_until)
    monkeypatch.setattr(group.runtime, "_sample", forbidden)
    try:
        async def execute():
            async with Execution(toy.backend, coverage=[group.runtime]):
                # Four pending attempts fit the declared capacity; no completions needed.
                await ClockCycles(toy.clock, 4)
        asyncio.run(execute())
        assert group.first.count(Events.high) == 4
        assert len(calls) == 1
    finally:
        toy.close()


def test_schema_and_contract_include_bin_parameters():
    class Longer(Completions):
        tag_one_fast = Bin.pattern(roundtrip.with_args(maximum=3))
    class Changed(ProtocolCoverage):
        completions = Longer()
    assert Changed.compile().schema.digest != ProtocolCoverage.compile().schema.digest
    assert Changed.compile().diff(ProtocolCoverage.compile())
    assert "WithinStep" in ProtocolCoverage.compile().explain()


@xtrigger(mode=ConditionMode.EACH_SAMPLE)
def always(pins: ProtocolBundle) -> bool:
    return True


class ConstantEvents(TemporalCoverPoint[ProtocolBundle]):
    every_sample = always


@covergroup(contract="test.constant-program/v1")
class ConstantCoverage(SignalCoverGroup[ProtocolBundle]):
    events = ConstantEvents()


@pytest.mark.parametrize("engine,strategy", MODES)
def test_constant_program_requires_no_dummy_signal(engine, strategy):
    toy = ToyResponder(engine=engine)
    coverage = ConstantCoverage(instance="test").bind(pins=toy.pins, sample=RisingEdge(toy.clock), strategy=strategy)
    try:
        async def execute():
            async with Execution(toy.backend, coverage=[coverage.runtime]):
                await ClockCycles(toy.clock, 2)
        asyncio.run(execute())
        assert coverage.events.count(ConstantEvents.every_sample) == 2
        assert coverage.runtime._binding["fields"] == {}
    finally:
        toy.close()


@xtrigger(sample=RisingEdge("clock"))
def response_result(pins: ProtocolBundle):
    response = signal_expr(pins.response.valid)
    return FSM(start="idle", states={
        "idle": State().when(signal_expr(pins.request.valid)).goto("pending"),
        "pending": State().when(response & signal_expr(pins.response.ready)).trigger("OK")
            .when(response).trigger("BAD"),
    })


class ResultCases(TemporalCoverPoint[ProtocolBundle]):
    ok = Bin.pattern(response_result, terminals=("OK",))
    bad = Bin.pattern(response_result, terminals=("BAD",))
    all = Bin.pattern(response_result, all_terminals=True)
    repeated = Bin.pattern(response_result, all_terminals=True, at_least=3)


@covergroup(contract="test.shared-results/v1")
class SharedResultCoverage(SignalCoverGroup[ProtocolBundle]):
    first = ResultCases()
    second = ResultCases()


def assert_execution_count(group, toy, expected):
    collector = group.runtime._collector
    assert len(collector.pattern_executions) == expected
    if collector.native:
        assert toy.backend._engine.CoverageExecutionCount(collector._native_handle()) == expected
    return collector


@pytest.mark.parametrize("engine,strategy", MODES)
def test_shared_fsm_routes_terminals_with_independent_counters_and_thresholds(engine, strategy, monkeypatch):
    calls = []
    def inspect_start(group, toy):
        collector = assert_execution_count(group, toy, 2)  # Eight bins, two point-local observations.
        assert collector.bin_patterns[("first", "ok")] is collector.bin_patterns[("first", "bad")]
        assert collector.bin_patterns[("first", "ok")] is not collector.bin_patterns[("second", "ok")]
        original = collector.matcher._advance_fsm
        def advance(trigger, state):
            calls.append(1)
            return original(trigger, state)
        monkeypatch.setattr(collector.matcher, "_advance_fsm", advance)
    report, progress = asyncio.run(manual_run(engine, strategy, [(1, 0, 1), (0, 1, 1), (1, 0, 0), (0, 1, 0)],
        group_type=SharedResultCoverage, diagnostics="summary", on_start=inspect_start))
    for name in ("first", "second"):
        item = point(report, name)
        assert item["counts"] == {"ok": 1, "bad": 1, "all": 2, "repeated": 2}
        assert item["coverage"] == 75
    assert progress["bins"] == {}
    if strategy == "native":
        assert calls == []
    else:
        assert len(calls) == 8  # Each point advances once per sample, rather than once per bin.
    for stats in report["diagnostics"]["patterns"]:
        assert stats["started"] == stats["completed"] == 2
        assert stats["unfinished_at_close"] == 0


@xtrigger(sample=RisingEdge("clock"))
def aged_response(pins: ProtocolBundle):
    response = signal_expr(pins.response.valid)
    return FSM(start="idle", states={
        "idle": State().when(signal_expr(pins.request.valid)).goto("first"),
        "first": State().when(response).trigger("FAST").otherwise().goto("later"),
        "later": State().when(response).trigger("SLOW"),
    })


class AgedCases(TemporalCoverPoint[ProtocolBundle]):
    fast = Bin.pattern(aged_response, terminals=("FAST",), overlap=True, max_active=3)
    slow = Bin.pattern(aged_response, terminals=("SLOW",), overlap=True, max_active=3)
    all = Bin.pattern(aged_response, all_terminals=True, overlap=True, max_active=3)
    both = Bin.pattern(aged_response, terminals=("FAST", "SLOW"), overlap=True, max_active=3)


@covergroup(contract="test.shared-overlap/v1")
class SharedOverlapCoverage(SignalCoverGroup[ProtocolBundle]):
    result = AgedCases()


@pytest.mark.parametrize("engine,strategy", MODES)
def test_shared_fsm_routes_multiple_completions_by_terminal(engine, strategy):
    report, _ = asyncio.run(manual_run(engine, strategy, [(1, 0), (1, 0), (1, 0), (0, 1)],
        group_type=SharedOverlapCoverage, diagnostics="summary",
        on_start=lambda group, toy: assert_execution_count(group, toy, 1)))
    assert point(report, "result")["counts"] == {"fast": 1, "slow": 2, "all": 3, "both": 3}
    for stats in report["diagnostics"]["patterns"]:
        assert stats["started"] == stats["completed"] == stats["peak_active"] == 3


@pytest.mark.parametrize("engine,strategy", MODES)
@pytest.mark.parametrize("clear", [True, False])
def test_shared_pending_fsm_is_cleared_once_for_abort_or_history_reset(engine, strategy, clear):
    report, _ = asyncio.run(manual_run(engine, strategy,
        [(1, 0, 0), (0, 0, int(not clear)), (0, 1, 0), (1, 0, 0), (0, 1, 0)],
        group_type=SharedResultCoverage, diagnostics="summary", abort=not clear,
        reset_at=1 if clear else None,
        on_start=lambda group, toy: assert_execution_count(group, toy, 2)))
    for name in ("first", "second"):
        assert point(report, name)["counts"] == {"ok": 0, "bad": 1, "all": 1, "repeated": 1}
    for stats in report["diagnostics"]["patterns"]:
        assert stats["started"] == 2 and stats["completed"] == 1
        assert stats["cleared" if clear else "aborted"] == 1


class PolicyCases(TemporalCoverPoint[ProtocolBundle]):
    short = Bin.pattern(roundtrip.with_args(maximum=2))
    long = Bin.pattern(roundtrip.with_args(maximum=4))
    repeated = Bin.pattern(roundtrip.with_args(maximum=4), at_least=3)
    overlapping = Bin.pattern(roundtrip, overlap=True, max_active=3)
    greater_capacity = Bin.pattern(roundtrip, overlap=True, max_active=4)


@covergroup(contract="test.shared-policy/v1")
class PolicyCoverage(SignalCoverGroup[ProtocolBundle]):
    cases = PolicyCases()


@pytest.mark.parametrize("engine,strategy", MODES)
def test_sharing_requires_identical_windows_overlap_and_capacity(engine, strategy):
    asyncio.run(manual_run(engine, strategy, [(0, 0)], group_type=PolicyCoverage,
        on_start=lambda group, toy: assert_execution_count(group, toy, 4)))


def test_explain_reports_shared_execution_without_changing_schema():
    compiled = ProtocolCoverage.compile()
    completions = next(p for p in compiled.review_dict()["points"] if p["name"] == "completions")
    assert next(e for e in completions["executions"] if e["owner"] == "bad_data")["bins"] == ["bad_data", "correct_data"]
    assert "execution 0 (checked_tag_one): bad_data, correct_data" in compiled.explain()
    assert compiled.schema.to_dict()["version"] == 3


class MixedCases(TemporalCoverPoint[ProtocolBundle]):
    path = delayed
    repeated_path = Bin.pattern(delayed, at_least=2)
    rising = enter
    repeated_rising = Bin.pattern(enter, at_least=2)
    high = level
    repeated_high = Bin.pattern(level, at_least=2)
    changed = change


@covergroup(contract="test.shared-mixed/v1")
class SharedMixedCoverage(SignalCoverGroup[ProtocolBundle]):
    cases = MixedCases()


@pytest.mark.parametrize("engine,strategy", MODES)
def test_sequence_and_expr_share_progress_without_double_consuming_samples(engine, strategy):
    report, _ = asyncio.run(manual_run(engine, strategy, [(1, 0), (1, 0), (1, 0), (0, 1)],
        group_type=SharedMixedCoverage, on_start=lambda group, toy: assert_execution_count(group, toy, 4)))
    assert point(report, "cases")["counts"] == {"path": 1, "repeated_path": 1, "rising": 1,
        "repeated_rising": 1, "high": 3, "repeated_high": 3, "changed": 2}


@pytest.mark.parametrize("engine,strategy", MODES)
def test_shared_fsm_gate_clears_one_point_without_affecting_another(engine, strategy):
    toy = ToyResponder(engine=engine)
    if toy.native:
        toy.clock.RemoveStepRisCbByDesc("_evaluate_native")
    else:
        toy.backend.on_phase = None
    bins = {"ok": BinSpec(PatternMatcher(response_result.program, terminals=("OK",))),
            "bad": BinSpec(PatternMatcher(response_result.program, terminals=("BAD",)))}
    group = CoverGroupDef("shared-gate", (
        CoverPointDef("gated", bins, iff=Iff("gate", (1,))), CoverPointDef("continuous", bins),
    )).instantiate("test").bind(trigger=RisingEdge(toy.clock), root=toy.pins, strategy=strategy,
        fields={"gate": toy.pins.request.ready, "request.valid": toy.pins.request.valid,
                "response.valid": toy.pins.response.valid, "response.ready": toy.pins.response.ready},
        diagnostics="summary")
    try:
        async def execute():
            async with Execution(toy.backend, coverage=[group]):
                if strategy == "native":
                    assert toy.backend._engine.CoverageExecutionCount(group._collector._native_handle()) == 2
                for request, response, gate in [(1, 0, 1), (0, 0, 0), (0, 1, 1)]:
                    toy.pins.request.valid.Set(request)
                    toy.pins.response.valid.Set(response)
                    toy.pins.response.ready.Set(1)
                    toy.pins.request.ready.Set(gate)
                    await ClockCycles(toy.clock, 1)
        asyncio.run(execute())
        report = group.report()
        assert point(report, "gated")["counts"] == {"ok": 0, "bad": 0}
        assert point(report, "continuous")["counts"] == {"ok": 1, "bad": 0}
        for stats in report["diagnostics"]["patterns"]:
            assert stats["cleared"] == int(stats["point"] == "gated")
    finally:
        toy.close()
