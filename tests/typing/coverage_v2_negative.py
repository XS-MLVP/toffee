"""Each EXPECT_ERROR line must be rejected; do not execute this file."""
from toffee.declarative import Bin, BinRule, CoverPoint
from coverage_v2_positive import Coverage, Sample, StatePoint, TakenPoint, coverage, fields
from coverage_v2_protocol_positive import requests, transactions

coverage.sample({"state": 1, "taken": True})  # EXPECT_ERROR
coverage.sample(Sample("busy", True))  # EXPECT_ERROR
coverage.state.count(TakenPoint.yes)  # EXPECT_ERROR
StatePoint.buisy  # EXPECT_ERROR
Coverage.staet  # EXPECT_ERROR
fields.select(lambda sample: sample.staet)  # EXPECT_ERROR
StatePoint(source=fields.select(lambda sample: sample.label))  # EXPECT_ERROR
boolean_field = fields.select(lambda sample: sample.taken)
StatePoint(source=boolean_field)  # EXPECT_ERROR

class InvalidPoint(CoverPoint[int]):
    wrong: BinRule[int] = Bin.values("busy")  # EXPECT_ERROR

transactions.sample(Sample(1, True))  # EXPECT_ERROR
transactions.sample(None)  # EXPECT_ERROR
requests.ready.count(StatePoint.busy)  # EXPECT_ERROR

from toffee import Bundle, RisingEdge, xtrigger
from toffee.ir import SequenceSpec, XExpr, signal_expr
from toffee.triggers import CompiledTrigger
from examples.coverage.declarative_protocol import Pin, ProtocolBundle
from examples.coverage.trigger_bins import roundtrip, ProtocolCoverage

class OtherBundle(Bundle):
    ready: Pin

@xtrigger()
def other_ready(pins: OtherBundle) -> XExpr:
    return signal_expr(pins.ready)

def bad_patterns(pins: ProtocolBundle, other: OtherBundle, live: ProtocolCoverage) -> None:
    roundtrip.with_args(maximum="8")  # EXPECT_ERROR
    roundtrip.with_args(maxmum=8)  # EXPECT_ERROR
    roundtrip.with_args(8)  # EXPECT_ERROR
    roundtrip.with_args(maximum=8).bind(other)  # EXPECT_ERROR
    live.bind(pins=other, sample=RisingEdge(pins.clock))  # EXPECT_ERROR
    live.completions.count(other_ready)  # EXPECT_ERROR
    live.completions.count(Bin.pattern(other_ready))  # EXPECT_ERROR
    live.completions.count("tag_one")  # EXPECT_ERROR
    Bin.pattern(roundtrip, at_least="10")  # EXPECT_ERROR
    roundtrip(pins, maximum="8")  # EXPECT_ERROR
    declared: SequenceSpec = roundtrip(pins)  # EXPECT_ERROR
    bound: CompiledTrigger = roundtrip  # EXPECT_ERROR
    _ = declared, bound


from examples.coverage.mixed_patterns import python_request, pins as live_fields
python_request.with_args(minimum_tag="2")  # EXPECT_ERROR
python_request.with_args(minmum_tag=2)  # EXPECT_ERROR
live_fields.signal(lambda p: p.request.valid, value_type="bool")  # EXPECT_ERROR

def wrong_python_root(other: OtherBundle) -> None:
    python_request(other)  # EXPECT_ERROR
    python_request.with_args(minimum_tag=2).bind(other)  # EXPECT_ERROR
