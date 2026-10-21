from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
import json

import pytest

from xreactor import CoverGroup as CoreGroup, ClockCycles, Execution, RisingEdge, XCommClockBackend
from xreactor.coverage import CoverGroupDef, CoverageDatabase, IllegalBinError
from xreactor.declarative import (
    Bin, CoverGroup, CoverPoint, CoverageFragment, CoverageReferenceError, Cross,
    DefinitionError, FieldRef, Fields, Iff, IllegalPolicy, SampleTypeError, covergroup, wire,
    OverlapPolicy,
)
from examples.coverage.declarative_pipeline import (
    PipelineSample, StatePoint, TakenPoint, PipelineCoverage,
    SplitStatePoint, SplitPipelineCoverage, TRACE,
)


@dataclass(frozen=True)
class Inner:
    value: int


@dataclass(frozen=True)
class Envelope:
    prediction: Inner
    checked: bool


def statistics(report):
    result = {name: report[name] for name in ("schema_digest", "samples", "gated", "coverage", "has_illegal")}
    for kind in ("points", "crosses"):
        result[kind] = [{key: value for key, value in item.items() if key != "provenance"}
                        for item in report[kind]]
    return result


def test_runtime_uses_one_existing_engine_and_independent_state():
    left = PipelineCoverage(instance="left", illegal_policy=IllegalPolicy.RECORD)
    right = PipelineCoverage(instance="right", illegal_policy=IllegalPolicy.RECORD)
    assert left.runtime is left.runtime and isinstance(left.runtime, CoreGroup)
    reference = PipelineCoverage.compile().schema.instantiate("reference", illegal_policy="record")
    for sample in TRACE:
        left.sample(sample)
        reference.sample(sample)
    assert statistics(left.report()) == statistics(reference.report())
    assert left.state.count(StatePoint.busy) == 3
    assert left.state.count(StatePoint.idle_to_done) == 1
    assert right.samples == right.state.count(StatePoint.busy) == 0
    assert left.snapshot().samples == len(TRACE)
    assert left.snapshot().to_dict() == left.report()


def test_inheritance_rebinds_cross_preserves_base_and_old_bin_reference():
    base = PipelineCoverage.compile()
    saved = base.schema_json()
    split = SplitPipelineCoverage(instance="split")
    split.sample(PipelineSample(5, True))
    assert split.state.count(SplitStatePoint.extra) == 1
    assert split.state.count(StatePoint.short) == 0
    assert split.state_taken.count(SplitPipelineCoverage.state.bin(SplitStatePoint.extra),
                                    SplitPipelineCoverage.taken.bin(TakenPoint.yes)) == 1
    assert PipelineCoverage.compile().schema_json() == saved
    assert base.schema_digest != SplitPipelineCoverage.compile().schema_digest


def test_review_and_diff_show_bin_and_cross_space_changes():
    before, after = PipelineCoverage.compile(), SplitPipelineCoverage.compile()
    changes = before.diff(after)
    assert any(item["path"] == "model.schema.points.state.bins.extra" and item["change"] == "added"
               for item in changes)
    assert any(item["path"].endswith("short.matcher.ranges") for item in changes)
    assert any(item["path"] == "model.schema.eligible_cross_bins.state_taken" for item in changes)
    assert before.diff(before) == []
    assert "StatePoint" in before.explain() and "state_taken" in before.explain()
    assert "extra" in after.explain()


@pytest.mark.parametrize("bad", [
    {"state": 1, "taken": True}, PipelineSample(True, True),
    PipelineSample(1, 1), PipelineSample("busy", True),
])
def test_invalid_sample_is_atomic_and_preserves_transition_history(bad):
    group = PipelineCoverage(instance="atomic")
    group.sample(PipelineSample(0, False))
    saved = group.report()
    with pytest.raises(SampleTypeError):
        group.sample(bad)
    assert group.report() == saved
    group.sample(PipelineSample(1, False))
    group.sample(PipelineSample(2, True))
    assert group.state.count(StatePoint.idle_to_done) == 1


def test_illegal_commits_existing_engine_statistics():
    group = PipelineCoverage(instance="illegal")
    with pytest.raises(IllegalBinError):
        group.sample(PipelineSample(255, False))
    assert group.samples == 1 and group.state.count(StatePoint.illegal) == 1


def test_nested_fields_gate_and_selector_are_definition_time_only():
    calls = []
    fields = Fields(Envelope)

    def select(sample):
        calls.append(1)
        return sample.prediction.value

    class ValuePoint(CoverPoint[int]):
        one = Bin.values(1)

    @covergroup(iff=Iff.truth(fields.select(lambda sample: sample.checked)))
    class Model(CoverGroup[Envelope]):
        value = ValuePoint(source=fields.select(select))

    model = Model(instance="nested")
    model.sample(Envelope(Inner(1), False))
    model.sample(Envelope(Inner(1), True))
    assert model.gated == 1 and model.value.count(ValuePoint.one) == 1
    assert calls == [1]
    saved = model.report()
    with pytest.raises(SampleTypeError):
        model.sample(Envelope(Inner(True), False))
    assert saved == model.report()


@pytest.mark.parametrize("selector", [lambda s: s.checked + 1, lambda s: s.checked and s.checked,
                                     lambda s: s.missing, lambda s: 1,
                                     lambda s: s.prediction.value if s.checked == True else s.checked])
def test_unsupported_selectors_fail_at_definition(selector):
    with pytest.raises(DefinitionError):
        Fields(Envelope).select(selector)


def test_missing_default_field_reports_group_and_slot():
    class Model(CoverGroup[PipelineSample]):
        missing = StatePoint()
    with pytest.raises(DefinitionError, match="E_FIELD.*Model.missing"):
        Model.compile()


def test_decorator_keeps_class_keyword_configuration():
    @covergroup(description="observed pipeline")
    class Model(CoverGroup[PipelineSample], schema_id="keyword.pipeline", contract="keyword.observed/v1"):
        state = StatePoint()
    compiled = Model.compile()
    assert compiled.schema.name == "keyword.pipeline"
    assert compiled.contract == "keyword.observed/v1"
    assert compiled.schema.description == "observed pipeline"


def test_untyped_bad_source_and_unknown_decorator_options_fail():
    with pytest.raises(DefinitionError, match="E_FIELD"):
        StatePoint(source="")
    with pytest.raises(DefinitionError, match="E_DECORATOR"):
        covergroup(desciption="typo")(PipelineCoverage)


def test_wrong_bin_type_reports_bin():
    class Broken(CoverPoint[int]):
        typo = Bin.values("busy")
    class Model(CoverGroup[PipelineSample]):
        state = Broken()
    with pytest.raises(DefinitionError, match="E_BIN_TYPE.*state.typo"):
        Model.compile()


def test_wrong_root_and_forged_field_ref_fail_compile():
    class Model(CoverGroup[PipelineSample]):
        state = StatePoint(source=Fields(Inner).select(lambda s: s.value))
    with pytest.raises(DefinitionError, match="E_FIELD_ROOT"):
        Model.compile()
    class Forged(CoverGroup[PipelineSample]):
        state = StatePoint(source=FieldRef(PipelineSample, ("taken",), int))
    with pytest.raises(DefinitionError, match="E_FIELD_TYPE"):
        Forged.compile()
    class BooleanPath(CoverGroup[PipelineSample]):
        state = StatePoint(source=Fields(PipelineSample).select(lambda s: s.taken))
    with pytest.raises(DefinitionError, match="E_FIELD_TYPE"):
        BooleanPath.compile()


def test_foreign_point_and_bin_references_fail():
    foreign = StatePoint()
    class BadCross(CoverGroup[PipelineSample]):
        state = StatePoint()
        taken = TakenPoint()
        wrong = Cross(state, foreign)
    with pytest.raises(DefinitionError, match="E_CROSS"):
        BadCross.compile()
    class Unrelated(CoverPoint[int]):
        busy = Bin.values(1)
    group = PipelineCoverage(instance="refs")
    with pytest.raises(CoverageReferenceError):
        group.state.count(Unrelated.busy)
    with pytest.raises(CoverageReferenceError):
        group.state_taken.count(PipelineCoverage.taken.bin(TakenPoint.yes),
                                PipelineCoverage.state.bin(StatePoint.busy))


def test_declarations_and_runtime_views_are_readonly():
    group = PipelineCoverage(instance="immutable")
    with pytest.raises(AttributeError):
        group.state = StatePoint()
    with pytest.raises(AttributeError):
        PipelineCoverage.state.weight = 5
    with pytest.raises(AttributeError):
        PipelineCoverage.state_taken.points = ()


def test_compiled_model_rejects_monkey_patch():
    class LocalPoint(CoverPoint[int]):
        first = Bin.values(0)
    class Model(CoverGroup[PipelineSample]):
        state = LocalPoint()
    Model.compile()
    LocalPoint.extra = Bin.values(1)
    with pytest.raises(DefinitionError, match="E_FROZEN"):
        Model.compile()


def test_equal_rule_replacement_and_sample_annotation_changes_invalidate_cache():
    class LocalPoint(CoverPoint[int]):
        first = Bin.values(0)
    class Model(CoverGroup[PipelineSample]):
        state = LocalPoint()
    Model.compile()
    LocalPoint.first = Bin.values(0)
    with pytest.raises(DefinitionError, match="E_FROZEN"):
        Model.compile()
    @dataclass(frozen=True)
    class LocalSample:
        state: int
    class AnotherPoint(CoverPoint[int]):
        first = Bin.values(0)
    class AnotherModel(CoverGroup[LocalSample]):
        state = AnotherPoint()
    AnotherModel.compile()
    LocalSample.__annotations__["state"] = bool
    with pytest.raises(DefinitionError, match="E_FROZEN"):
        AnotherModel.compile()


def test_reserved_bin_names_are_rejected():
    class BadPoint(CoverPoint[int]):
        bin = Bin.values(0)
    class Model(CoverGroup[PipelineSample]):
        state = BadPoint()
    with pytest.raises(DefinitionError, match="E_RESERVED.*state.bin"):
        Model.compile()


def test_reused_point_and_bin_fail_with_clear_diagnostics():
    reused = StatePoint()
    class Model(CoverGroup[PipelineSample]):
        state = reused
        other = reused
    with pytest.raises(DefinitionError, match="E_REUSED_POINT"):
        Model.compile()
    bin = Bin.values(0)
    class Duplicate(CoverPoint[int]):
        first = bin
        second = bin
    class BadBin(CoverGroup[PipelineSample]):
        state = Duplicate()
    with pytest.raises(DefinitionError, match="E_REUSED_BIN"):
        BadBin.compile()


def test_explicit_fragments_compose_and_conflicts_are_rejected():
    class StateFragment(CoverageFragment[PipelineSample]):
        state = StatePoint()
    class TakenFragment(CoverageFragment[PipelineSample]):
        taken = TakenPoint()
    class Model(StateFragment, TakenFragment, CoverGroup[PipelineSample]):
        both = Cross(StateFragment.state, TakenFragment.taken)
    group = Model(instance="fragment")
    group.sample(PipelineSample(1, True))
    assert group.state.count(StatePoint.busy) == 1
    class AnotherState(CoverageFragment[PipelineSample]):
        state = StatePoint()
    class Conflict(StateFragment, AnotherState, CoverGroup[PipelineSample]):
        pass
    with pytest.raises(DefinitionError, match="E_CONFLICT"):
        Conflict.compile()
    class Resolved(StateFragment, AnotherState, CoverGroup[PipelineSample]):
        state = StatePoint()
    assert Resolved(instance="resolved").samples == 0
    class Left(StateFragment):
        pass
    class Right(StateFragment):
        pass
    class Diamond(Left, Right, CoverGroup[PipelineSample]):
        pass
    assert len(Diamond.compile().schema.points) == 1


def test_fragments_with_different_sample_contracts_fail():
    class Other(CoverageFragment[Inner]):
        pass
    class Model(Other, CoverGroup[PipelineSample]):
        state = StatePoint()
    with pytest.raises(DefinitionError, match="E_TYPE"):
        Model.compile()


def test_cross_selections_are_rebound_and_validated():
    class Model(CoverGroup[PipelineSample]):
        state = StatePoint()
        taken = TakenPoint()
        both = Cross(state, taken, include=((state.bin(StatePoint.busy), taken.bin(TakenPoint.yes)),))
    group = Model(instance="selected")
    group.sample(PipelineSample(1, True))
    assert group.both.count(Model.state.bin(StatePoint.busy), Model.taken.bin(TakenPoint.yes)) == 1
    class Invalid(CoverGroup[PipelineSample]):
        state = StatePoint()
        taken = TakenPoint()
        both = Cross(state, taken, include=((taken.bin(TakenPoint.yes), state.bin(StatePoint.busy)),))
    with pytest.raises(DefinitionError, match="E_CROSS"):
        Invalid.compile()


def test_reset_and_clear_history_keep_instance_state_separate():
    group = PipelineCoverage(instance="reset")
    group.sample(PipelineSample(0, False))
    group.clear_history()
    group.sample(PipelineSample(1, True))
    group.sample(PipelineSample(2, True))
    assert group.state.count(StatePoint.idle_to_done) == 0
    assert group.state.count(StatePoint.idle) == 1
    group.reset()
    assert group.samples == group.state.count(StatePoint.idle) == 0


def test_enum_and_string_domains_work_for_manual_samples():
    class Phase(Enum):
        MAIN = "main"
    @dataclass(frozen=True)
    class Sample:
        phase: Phase
    # Assign concrete runtime annotation because this local class uses postponed annotations.
    Sample.__annotations__["phase"] = Phase
    class PhasePoint(CoverPoint[Phase]):
        main = Bin.values(Phase.MAIN)
    class Model(CoverGroup[Sample]):
        phase = PhasePoint()
    group = Model(instance="enum")
    group.sample(Sample(Phase.MAIN))
    assert group.phase.count(PhasePoint.main) == 1
    with pytest.raises(SampleTypeError):
        group.sample(Sample("main"))


def test_core_database_accepts_v2_runtime_reports():
    group = PipelineCoverage(instance="database")
    group.sample(PipelineSample(0, False))
    database = CoverageDatabase([group.runtime])
    assert database.report()["groups"][0]["samples"] == 1
    restored = CoreGroup.from_report(group.report())
    assert statistics(restored.report()) == statistics(group.report())


async def clock_run(strategy, model=PipelineCoverage, trace=TRACE):
    import xspcomm
    clock = xspcomm.XClock(lambda _: 0)
    backend = XCommClockBackend(clock)
    state = xspcomm.XData(8, xspcomm.XData.InOut)
    taken = xspcomm.XData(1, xspcomm.XData.InOut)
    fields = Fields(PipelineSample)
    group = model(instance="native", illegal_policy=IllegalPolicy.RECORD)
    group.bind(trigger=RisingEdge(clock), strategy=strategy, fields=(
        wire(fields.select(lambda s: s.state), state, source_id="dut.state"),
        wire(fields.select(lambda s: s.taken), taken, source_id="dut.taken"),
    ))
    try:
        async with Execution(backend, coverage=[group.runtime]):
            for sample in trace:
                state.Set(sample.state)
                taken.Set(int(sample.taken))
                await ClockCycles(clock, 1)
        return group.report()
    finally:
        assert backend._engine.ActiveCount() == 0
        backend.close()


@pytest.mark.parametrize("model", [PipelineCoverage, SplitPipelineCoverage])
def test_native_python_and_manual_statistics_match(model):
    reference = model(instance="manual", illegal_policy=IllegalPolicy.RECORD)
    for sample in TRACE:
        reference.sample(sample)
    python = asyncio.run(clock_run("python", model))
    native = asyncio.run(clock_run("native", model))
    assert statistics(reference.report()) == statistics(python) == statistics(native)
    assert python["sampling_backend"] == "python" and native["sampling_backend"] == "native"
    assert python["sampling_contract"] == native["sampling_contract"]


def test_native_does_not_enter_python_transaction_validation(monkeypatch):
    import xreactor.declarative._runtime as runtime
    def unexpected(*args):
        raise AssertionError("native sampling entered the Python transaction path")
    monkeypatch.setattr(runtime, "_validated_snapshot", unexpected)
    assert asyncio.run(clock_run("native"))["samples"] == len(TRACE)


def test_native_nested_gate_wide_mask_and_transition_match_python():
    import xspcomm
    high = 1 << 100
    fields = Fields(Envelope)
    class WidePoint(CoverPoint[int]):
        zero = Bin.values(0)
        high = Bin.masked(1 << 100, 1 << 100, width=128)
        path = Bin.transition(1 << 100, (1 << 100) + 1, (1 << 100) + 2)
    @covergroup(schema_id="nested.wide", contract="nested.observed/v1",
                iff=Iff.truth(fields.select(lambda s: s.checked)))
    class Model(CoverGroup[Envelope]):
        value = WidePoint(source=fields.select(lambda s: s.prediction.value), overlap=OverlapPolicy.ALLOW)
        checked = TakenPoint()
        both = Cross(value, checked)
    trace = tuple(Envelope(Inner(value), checked) for value, checked in (
        (high, True), (high + 1, True), (high + 2, True),
        (high, True), (high + 1, False), (high + 2, True), (0, True)))
    reference = Model(instance="manual")
    for sample in trace:
        reference.sample(sample)
    async def run(strategy):
        clock = xspcomm.XClock(lambda _: 0)
        backend = XCommClockBackend(clock)
        value = xspcomm.XData(128, xspcomm.XData.InOut)
        checked = xspcomm.XData(1, xspcomm.XData.InOut)
        group = Model(instance=strategy)
        group.bind(trigger=RisingEdge(clock), strategy=strategy, fields=(
            wire(fields.select(lambda s: s.prediction.value), value, source_id="dut.prediction.value"),
            wire(fields.select(lambda s: s.checked), checked, source_id="dut.checked"),
        ))
        try:
            async with Execution(backend, coverage=[group.runtime]):
                for sample in trace:
                    value.Set(sample.prediction.value)
                    checked.Set(int(sample.checked))
                    await ClockCycles(clock, 1)
            assert group.value.count(WidePoint.path) == 1
            return group.report()
        finally:
            assert backend._engine.ActiveCount() == 0
            backend.close()
    assert statistics(reference.report()) == statistics(asyncio.run(run("python"))) == statistics(asyncio.run(run("native")))


def test_strict_native_rejection_auto_fallback_and_cleanup():
    class NegativeState(StatePoint):
        negative = Bin.values(-1)
    class Model(PipelineCoverage):
        state = NegativeState()
    with pytest.raises(NotImplementedError, match="unsigned"):
        asyncio.run(clock_run("native", Model))
    report = asyncio.run(clock_run("auto", Model))
    assert report["sampling_backend"] == "python" and "unsigned" in report["sampling_fallback"]


def test_binding_rejects_bool_width_duplicate_missing_and_wrong_root():
    import xspcomm
    clock = xspcomm.XClock(lambda _: 0)
    byte = xspcomm.XData(8, xspcomm.XData.InOut)
    bit = xspcomm.XData(1, xspcomm.XData.InOut)
    fields = Fields(PipelineSample)
    state = fields.select(lambda s: s.state)
    taken = fields.select(lambda s: s.taken)
    group = PipelineCoverage(instance="binding")
    with pytest.raises(DefinitionError, match="E_BINDING_WIDTH"):
        group.bind(trigger=RisingEdge(clock), fields=(wire(state, byte), wire(taken, byte)))
    with pytest.raises(DefinitionError, match="E_DUPLICATE_BINDING"):
        group.bind(trigger=RisingEdge(clock), fields=(wire(state, byte), wire(state, byte)))
    with pytest.raises(DefinitionError, match="E_MISSING_BINDING"):
        group.bind(trigger=RisingEdge(clock), fields=(wire(state, byte),))
    with pytest.raises(DefinitionError, match="E_BINDING_ROOT"):
        group.bind(trigger=RisingEdge(clock), fields=(wire(Fields(Inner).select(lambda s: s.value), byte),))
    assert group.samples == 0
    assert group.binding_contract_json() is None
    group.bind(trigger=RisingEdge(clock), fields=(wire(state, byte, source_id="dut.state"),
                                                wire(taken, bit, source_id="dut.taken")))
    binding = json.loads(group.binding_contract_json())
    assert binding["opaque_sources"] == []
    assert binding["bindings"] == [{"field": "state", "source_id": "dut.state", "width": 8},
                                    {"field": "taken", "source_id": "dut.taken", "width": 1}]
