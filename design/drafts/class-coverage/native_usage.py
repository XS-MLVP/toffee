"""Design-only typed declarations and native binding; no Enum is required.

This file uses api.pyi, which deliberately has no runtime implementation.
The independent runnable subset is native_probe.py, not this file.
"""

from dataclasses import dataclass
from typing import assert_type

from api import (
    Bin,
    BoundPoint,
    CoverGroup,
    CoverPoint,
    CoverageFragment,
    Cross,
    ExecutionPlan,
    Fields,
    SignalBinding,
    covergroup,
    coverpoint,
    wire,
)


@dataclass(frozen=True)
class PipelineSample:
    state: int
    taken: bool


@coverpoint
class StatePoint(CoverPoint[int]):
    idle = Bin.values(0)
    busy = Bin.values(1, at_least=2)
    done = Bin.values(2)
    idle_to_done = Bin.transition(0, 1, 2)


@coverpoint(description="Prediction direction")
class TakenPoint(CoverPoint[bool]):
    yes = Bin.values(True)
    no = Bin.values(False)


class StateFragment(CoverageFragment[PipelineSample]):
    state = StatePoint()


class TakenFragment(CoverageFragment[PipelineSample]):
    taken = TakenPoint()


@covergroup(
    schema_id="bpu.pipeline",
    contract="bpu.stable-pipeline/v1",
    description="Pipeline state and prediction direction",
)
class PipelineCoverage(StateFragment, TakenFragment, CoverGroup[PipelineSample]):
    state_taken = Cross(StateFragment.state, TakenFragment.taken)


fields = Fields(PipelineSample)


def configure_native(
    clock_trigger: object,
    state_xdata: object,
    taken_xdata: object,
    backend: object,
) -> PipelineCoverage:
    coverage = PipelineCoverage(instance="kunminghu_v3.bpu.pipeline")
    state_wire = wire(fields.select(lambda sample: sample.state), state_xdata)
    taken_wire = wire(fields.select(lambda sample: sample.taken), taken_xdata)
    assert_type(state_wire, SignalBinding[int])
    assert_type(taken_wire, SignalBinding[bool])
    assert_type(
        coverage.bind(
            trigger=clock_trigger,
            fields=(state_wire, taken_wire),
            strategy="native",
        ),
        PipelineCoverage,
    )
    assert_type(coverage.plan(backend), ExecutionPlan)
    assert_type(PipelineCoverage.state, StatePoint)
    assert_type(coverage.state, BoundPoint[int])
    assert_type(coverage.taken, BoundPoint[bool])
    return coverage
