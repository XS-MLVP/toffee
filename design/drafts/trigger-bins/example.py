"""Type-checkable example against the implemented experimental API."""

from xreactor import RisingEdge
from xreactor.ir import Sequence, SequenceSpec, Wait, Within, XExpr, signal_expr
from xreactor.triggers import CompiledTrigger, ConditionMode
from examples.coverage.declarative_protocol import ProtocolBundle

from xreactor import xtrigger, TriggerDefinition
from xreactor.declarative import Bin, PatternBin, SignalCoverGroup, TemporalCoverPoint, covergroup


@xtrigger(mode=ConditionMode.EACH_SAMPLE)
def read_accepted(pins: ProtocolBundle) -> XExpr:
    return (
        signal_expr(pins.request.valid) & signal_expr(pins.request.ready)
        & (signal_expr(pins.request.opcode) == 0)
    )


@xtrigger()
def roundtrip(pins: ProtocolBundle, *, maximum: int = 4) -> SequenceSpec:
    # Only valid when there is at most one outstanding request.
    return Sequence(
        Wait(signal_expr(pins.request.valid) & signal_expr(pins.request.ready)),
        Within(1, maximum, signal_expr(pins.response.valid) & signal_expr(pins.response.ready)),
    )


class RequestPoint(TemporalCoverPoint[ProtocolBundle]):
    read = read_accepted


class RoundTripPoint(TemporalCoverPoint[ProtocolBundle]):
    # Common annotation lets a subclass replace a direct definition with a rule.
    within_four_cycles: PatternBin[ProtocolBundle] = roundtrip
    repeated = Bin.pattern(roundtrip, at_least=10)


class ExtendedRoundTripPoint(RoundTripPoint):
    within_four_cycles: PatternBin[ProtocolBundle] = Bin.pattern(roundtrip, at_least=2)
    within_eight_cycles = roundtrip.with_args(maximum=8)


@covergroup(schema_id="protocol.patterns")
class ProtocolCoverage(SignalCoverGroup[ProtocolBundle]):
    request = RequestPoint()
    roundtrip = RoundTripPoint()


class ExtendedProtocolCoverage(ProtocolCoverage):
    roundtrip = ExtendedRoundTripPoint()


def bind_and_read(pins: ProtocolBundle, coverage: ProtocolCoverage) -> int:
    coverage.bind(pins=pins, sample=RisingEdge(pins.clock), strategy="native")
    return coverage.roundtrip.count(RoundTripPoint.within_four_cycles)


def bind_specialization(pins: ProtocolBundle) -> CompiledTrigger:
    pattern: TriggerDefinition[ProtocolBundle, ...] = roundtrip.with_args(maximum=8)
    return pattern.bind(pins, sample=RisingEdge(pins.clock))


async def wait_for_completion(pins: ProtocolBundle) -> None:
    # Resolves the Execution default sample, just like the existing trigger path.
    await roundtrip(pins, maximum=4)
