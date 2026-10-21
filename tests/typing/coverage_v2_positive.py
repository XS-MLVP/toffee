from dataclasses import dataclass
from typing import assert_type

from xreactor.declarative import (
    Bin, BinRule, BoundPoint, CompiledGroup, CoverGroup, CoverPoint, CoverageSnapshot,
    Cross, FieldRef, Fields, SignalBinding, covergroup, coverpoint, wire,
)


@dataclass(frozen=True)
class Sample:
    state: int
    taken: bool
    label: str = ""


@coverpoint
class StatePoint(CoverPoint[int]):
    idle = Bin.values(0)
    busy = Bin.values(1)


@coverpoint
class TakenPoint(CoverPoint[bool]):
    yes = Bin.values(True)
    no = Bin.values(False)


@covergroup(schema_id="typing.pipeline")
class Coverage(CoverGroup[Sample]):
    state = StatePoint()
    taken = TakenPoint()
    both = Cross(state, taken)


fields = Fields(Sample)
assert_type(fields.select(lambda s: s.state), FieldRef[int])
assert_type(fields.select(lambda s: s.taken), FieldRef[bool])
assert_type(Coverage.state, StatePoint)
assert_type(StatePoint.busy, BinRule[int])
assert_type(Coverage.compile(), CompiledGroup[Sample])
coverage = Coverage(instance="typing")
coverage.sample(Sample(1, True))
assert_type(coverage.state, BoundPoint[int])
assert_type(coverage.state.count(StatePoint.busy), int)
assert_type(coverage.snapshot(), CoverageSnapshot)
assert_type(wire(fields.select(lambda s: s.state), object()), SignalBinding[int])
