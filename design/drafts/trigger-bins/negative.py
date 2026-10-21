"""Each EXPECT_ERROR line must be rejected by the draft's type checker."""

from xreactor import Bundle, RisingEdge
from xreactor.ir import SequenceSpec, XExpr, signal_expr
from xreactor.triggers import CompiledTrigger
from examples.coverage.declarative_protocol import Pin, ProtocolBundle

from xreactor import xtrigger
from xreactor.declarative import Bin
from example import ProtocolCoverage, roundtrip


class OtherBundle(Bundle):
    ready: Pin


@xtrigger()
def other_ready(pins: OtherBundle) -> XExpr:
    return signal_expr(pins.ready)


def invalid(pins: ProtocolBundle, other: OtherBundle, coverage: ProtocolCoverage) -> None:
    roundtrip.with_args(maximum="8")  # EXPECT_ERROR
    roundtrip.with_args(maxmum=8)  # EXPECT_ERROR
    roundtrip.with_args(8)  # EXPECT_ERROR
    roundtrip.with_args(maximum=8).bind(other)  # EXPECT_ERROR
    coverage.bind(pins=other, sample=RisingEdge(pins.clock))  # EXPECT_ERROR
    coverage.roundtrip.count(other_ready)  # EXPECT_ERROR
    coverage.roundtrip.count(Bin.pattern(other_ready))  # EXPECT_ERROR
    coverage.roundtrip.count("within_four_cycles")  # EXPECT_ERROR
    Bin.pattern(roundtrip, at_least="10")  # EXPECT_ERROR
    roundtrip(pins, maximum="8")  # EXPECT_ERROR
    declared: SequenceSpec = roundtrip(pins)  # EXPECT_ERROR
    bound: CompiledTrigger = roundtrip  # EXPECT_ERROR
    # Avoid unrelated unused-local diagnostics in this negative fixture.
    _ = declared, bound
