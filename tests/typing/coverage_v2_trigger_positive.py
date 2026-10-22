from typing import assert_type

from toffee import bits, RisingEdge, TriggerDefinition, XExpr
from toffee.ir import signal_expr
from toffee.declarative import Bin, BinRule, BoundPoint, PatternBin
from toffee.triggers import CompiledTrigger
from examples.coverage.declarative_protocol import ProtocolBundle
from examples.coverage.trigger_bins import (
    Completions, ProtocolCoverage, accepted, roundtrip,
)

coverage = ProtocolCoverage(instance="typing.protocol")
assert_type(coverage.completions, BoundPoint[ProtocolBundle])
assert_type(Completions.tag_one_fast, BinRule[ProtocolBundle])


def observe(pins: ProtocolBundle) -> int:
    data = signal_expr(pins.response.data)
    assert_type(data.as_signed(), XExpr)
    assert_type(data.resize(129) + bits(1, 129), XExpr)
    assert_type(data.bit_and(255), XExpr)
    assert_type(accepted(pins), CompiledTrigger)
    assert_type(roundtrip(pins, maximum=3), CompiledTrigger)
    fixed: TriggerDefinition[ProtocolBundle, ...] = roundtrip.with_args(maximum=3)
    assert_type(fixed.bind(pins), CompiledTrigger)
    rule: PatternBin[ProtocolBundle] = Bin.pattern(fixed, at_least=2)
    _ = rule
    coverage.bind(pins=pins, sample=RisingEdge(pins.clock))
    return coverage.completions.count(Completions.tag_one)


from toffee.decorators import PythonTriggerDefinition
from toffee.declarative import FieldRef
from toffee.triggers import PythonPredicateTrigger
from examples.coverage.mixed_patterns import InterfaceWithPython, PythonRequests, pins as live_fields, python_request

mixed = InterfaceWithPython(instance="typing.mixed")
assert_type(mixed.request_valid, BoundPoint[bool])
assert_type(mixed.python_requests, BoundPoint[ProtocolBundle])
assert_type(live_fields.signal(lambda p: p.request.valid, value_type=bool), FieldRef[bool])

def observe_python(pins: ProtocolBundle) -> int:
    assert_type(python_request(pins, minimum_tag=2), PythonPredicateTrigger)
    fixed: PythonTriggerDefinition[ProtocolBundle, ...] = python_request.with_args(minimum_tag=2)
    assert_type(fixed.bind(pins), PythonPredicateTrigger)
    rule: PatternBin[ProtocolBundle] = Bin.pattern(fixed)
    _ = rule
    mixed.bind(pins=pins, sample=RisingEdge(pins.clock))
    return mixed.python_requests.count(PythonRequests.any_tag)
