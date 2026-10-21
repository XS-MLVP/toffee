"""Mixed point kinds and opaque Python predicates preserve sampling semantics."""
from __future__ import annotations

import asyncio

import pytest

from xreactor import ClockCycles, ConditionMode, Execution, RisingEdge, pytrigger
from xreactor.coverage import CoverGroup as CoreGroup
from xreactor.declarative import (
    Bin, DefinitionError, Iff, PatternCoverPoint, SignalCoverGroup, covergroup,
)
from xreactor.ir import program_from_dict, program_to_dict
from examples.coverage.declarative_protocol import ProtocolBundle, ToyResponder
from examples.coverage.mixed_patterns import (
    InterfaceCoverage, InterfaceWithPython, PythonRequests, pins, python_request,
)


ROWS = [(1, 0, 1), (1, 0, 1), (0, 1, 1), (1, 0, 0), (0, 1, 0)]
MODES = [("memory", "python"), ("xcomm", "python"), ("xcomm", "auto")]


def counts(report, name):
    return next(point["counts"] for point in report["points"] if point["name"] == name)


async def observe(model, engine, strategy, rows=ROWS, *, abort=False, on_start=None):
    toy = ToyResponder(engine=engine)
    if toy.native:
        toy.clock.RemoveStepRisCbByDesc("_evaluate_native")
    else:
        toy.backend.on_phase = None
    try:
        group = model(instance="interface").bind(
            pins=toy.pins, sample=RisingEdge(toy.clock), strategy=strategy,
            abort=toy.pins.response.ready if abort else None,
        )
        toy.pins.request.tag.Set(1)
        async with Execution(toy.backend, coverage=[group.runtime]):
            if on_start:
                on_start(group, toy)
            for request, response, ready in rows:
                toy.pins.request.valid.Set(request)
                toy.pins.response.valid.Set(response)
                toy.pins.response.ready.Set(ready)
                await ClockCycles(toy.clock, 1)
            report = group.report()
        assert toy.backend.watcher_count == 0
        if toy.native:
            assert not toy.clock.IsControlled()
        return report
    finally:
        toy.close()


@pytest.mark.parametrize("engine,strategy", MODES + [("xcomm", "native")])
def test_value_pattern_and_cross_share_one_group(engine, strategy):
    def inspect(group, toy):
        if group.runtime._collector.native:
            assert toy.backend.watcher_count == 1
            handle = group.runtime._collector._native_handle()
            assert toy.backend._engine.CoverageExecutionCount(handle) == 1
    report = asyncio.run(observe(InterfaceCoverage, engine, strategy, on_start=inspect))
    assert counts(report, "request_valid") == {"low": 2, "high": 3}
    assert counts(report, "response_ready") == {"low": 2, "high": 3}
    assert counts(report, "responses") == {"accepted": 1, "blocked": 1, "completed": 2}
    assert sum(report["crosses"][0]["counts"].values()) == 5
    assert report["sampling_backend"] == ("native" if strategy in ("auto", "native") else "python")


@pytest.mark.parametrize("engine,strategy", MODES)
def test_pytrigger_bins_keep_values_crosses_and_compiled_fsm_counts(engine, strategy):
    report = asyncio.run(observe(InterfaceWithPython, engine, strategy))
    assert counts(report, "python_requests") == {"any_tag": 3, "large_tag": 0}
    assert counts(report, "responses") == {"accepted": 1, "blocked": 1, "completed": 2}
    assert sum(report["crosses"][0]["counts"].values()) == 5
    assert report["sampling_backend"] == "python"
    if strategy == "auto":
        assert "pytrigger bins" in report["sampling_fallback"]
    restored = CoreGroup.from_report(report)
    assert restored.report()["points"] == report["points"]
    assert restored.definition.digest == report["schema_digest"]


def test_explicit_native_pytrigger_rejection_releases_clock_and_programs():
    def no_start(*args):
        pytest.fail("native-only group with Python bins must fail before sampling")
    with pytest.raises(NotImplementedError, match="pytrigger bins"):
        asyncio.run(observe(InterfaceWithPython, "xcomm", "native", on_start=no_start))


@pytrigger(sample=RisingEdge("clock"), mode=ConditionMode.ENTER)
def entered(p: ProtocolBundle):
    return bool(p.request.valid.U())


@pytrigger(sample=RisingEdge("clock"), mode=ConditionMode.CHANGE)
def changed(p: ProtocolBundle):
    return bool(p.request.valid.U())


class PythonModes(PatternCoverPoint[ProtocolBundle]):
    enter = entered
    change = changed
    level = python_request
    repeat = Bin.pattern(python_request, at_least=4)


@covergroup(contract="python-modes/v1")
class ModeCoverage(SignalCoverGroup[ProtocolBundle]):
    modes = PythonModes()


@pytest.mark.parametrize("engine,strategy", MODES)
def test_python_condition_modes_parameterization_and_object_queries(engine, strategy):
    report = asyncio.run(observe(ModeCoverage, engine, strategy))
    assert counts(report, "modes") == {"enter": 2, "change": 4, "level": 3, "repeat": 3}


@covergroup(contract="python-gated/v1")
class GatedCoverage(SignalCoverGroup[ProtocolBundle]):
    modes = PythonModes(iff=Iff.truth(pins.signal(lambda p: p.response.ready, value_type=bool)))


@pytest.mark.parametrize("engine,strategy", MODES)
def test_gates_clear_python_predicate_history(engine, strategy):
    report = asyncio.run(observe(GatedCoverage, engine, strategy, [(1, 0, 1), (1, 0, 0), (1, 0, 1)]))
    assert counts(report, "modes") == {"enter": 2, "change": 2, "level": 2, "repeat": 2}


@pytest.mark.parametrize("engine,strategy", MODES)
def test_abort_clears_python_predicate_history(engine, strategy):
    report = asyncio.run(observe(ModeCoverage, engine, strategy, [(1, 0, 0), (1, 0, 1), (1, 0, 0)], abort=True))
    assert counts(report, "modes") == {"enter": 2, "change": 2, "level": 2, "repeat": 2}


def test_opaque_predicates_require_contract_and_do_not_deserialize_executable_code():
    class MissingContract(SignalCoverGroup[ProtocolBundle]):
        requests = PythonRequests()
    with pytest.raises(DefinitionError, match="explicit versioned observer contract"):
        MissingContract.compile()
    serialized = program_to_dict(python_request.program)
    assert "_predicate" not in serialized
    with pytest.raises(RuntimeError, match="must be rebound"):
        program_from_dict(serialized).evaluate(None)


def test_live_field_typos_are_rejected_during_definition():
    # Bundle deliberately permits dynamic attributes; its declaration selector validates annotations.
    with pytest.raises(DefinitionError, match="field does not exist"):
        pins.signal(lambda p: p.request.valdi)


@pytest.mark.parametrize("engine,strategy", MODES)
def test_shared_python_predicate_runs_once_per_sample(engine, strategy):
    calls = []
    @pytrigger(mode=ConditionMode.EACH_SAMPLE)
    def predicate(p: ProtocolBundle):
        calls.append(1)
        return len(calls) % 2 == 1
    class Cases(PatternCoverPoint[ProtocolBundle]):
        first = predicate
        second = Bin.pattern(predicate, at_least=4)
    @covergroup(contract="shared-python/v1")
    class Coverage(SignalCoverGroup[ProtocolBundle]):
        cases = Cases()
    def before_sampling(group, toy):
        assert calls == []
    report = asyncio.run(observe(Coverage, engine, strategy, on_start=before_sampling))
    assert len(calls) == 5
    assert counts(report, "cases") == {"first": 3, "second": 3}


def test_parameterized_pytrigger_remains_awaitable_and_can_sample_value_coverage():
    from xreactor.declarative import CoverGroup, CoverPoint, Fields, wire
    from dataclasses import dataclass
    @dataclass(frozen=True)
    class Snapshot:
        tag: int
    class Tags(CoverPoint[int]):
        two = Bin.values(2)
    @covergroup(contract="python-sample/v1")
    class Coverage(CoverGroup[Snapshot]):
        tag = Tags()
    async def run():
        toy = ToyResponder(engine="xcomm")
        toy.clock.RemoveStepRisCbByDesc("_evaluate_native")
        try:
            toy.pins.request.valid.Set(1)
            toy.pins.request.tag.Set(2)
            trigger = python_request.with_args(minimum_tag=2).bind(toy.pins)
            group = Coverage(instance="dut").bind(trigger=trigger, strategy="auto", fields=(
                wire(Fields(Snapshot).select(lambda s: s.tag), toy.pins.request.tag),
            ))
            async with Execution(toy.backend, coverage=[group.runtime]):
                await trigger
                await ClockCycles(toy.clock, 2)
            assert group.tag.count(Tags.two) == 3
            assert not toy.clock.IsControlled()
        finally:
            toy.close()
    asyncio.run(run())


def test_different_closures_with_the_same_name_do_not_share_predicate_results():
    def definition(result):
        @pytrigger(mode=ConditionMode.EACH_SAMPLE)
        def predicate(p: ProtocolBundle):
            return result
        return predicate
    class Cases(PatternCoverPoint[ProtocolBundle]):
        yes = definition(True)
        no = definition(False)
    @covergroup(contract="closures/v1")
    class Coverage(SignalCoverGroup[ProtocolBundle]):
        cases = Cases()
    report = asyncio.run(observe(Coverage, "xcomm", "auto"))
    assert counts(report, "cases") == {"yes": 5, "no": 0}


@pytest.mark.parametrize("asynchronous", [False, True])
def test_predicate_failure_cleans_up_execution(asynchronous):
    # Use factories with a concrete input annotation, like ordinary user definitions.
    if asynchronous:
        @pytrigger()
        async def broken(p: ProtocolBundle):
            return True
    else:
        @pytrigger()
        def broken(p: ProtocolBundle):
            raise ValueError("predicate failed")
    class Cases(PatternCoverPoint[ProtocolBundle]):
        failure = broken
    @covergroup(contract="errors/v1")
    class Coverage(SignalCoverGroup[ProtocolBundle]):
        cases = Cases()
    with pytest.raises((TypeError, ValueError), match="synchronous|predicate failed"):
        asyncio.run(observe(Coverage, "xcomm", "auto"))
