"""One interface group with value points, a shared FSM, a cross and optional Python bins."""
from __future__ import annotations

from xreactor import ConditionMode, FSM, RisingEdge, State, pytrigger, xtrigger
from xreactor.declarative import (
    Bin, CoverPoint, Cross, Fields, PatternCoverPoint, SignalCoverGroup, covergroup,
)
from xreactor.ir import signal_expr
from examples.coverage.declarative_protocol import ProtocolBundle


@xtrigger(sample=RisingEdge("clock"))
def response_result(pins: ProtocolBundle):
    response = signal_expr(pins.response.valid)
    return FSM(start="idle", states={
        "idle": State().when(signal_expr(pins.request.valid)).goto("pending"),
        "pending": State()
            .when(response & signal_expr(pins.response.ready)).trigger("ACCEPTED")
            .when(response).trigger("BLOCKED"),
    })


@pytrigger(sample=RisingEdge("clock"), mode=ConditionMode.EACH_SAMPLE)
def python_request(pins: ProtocolBundle, *, minimum_tag: int = 0):
    # An ordinary Python predicate can consult Python models or use Python control flow.
    return bool(pins.request.valid.U()) and pins.request.tag.U() >= minimum_tag


class Level(CoverPoint[bool]):
    low = Bin.values(False)
    high = Bin.values(True)


class Responses(PatternCoverPoint[ProtocolBundle]):
    accepted = Bin.pattern(response_result, terminals=("ACCEPTED",))
    blocked = Bin.pattern(response_result, terminals=("BLOCKED",))
    completed = Bin.pattern(response_result, all_terminals=True)


pins = Fields(ProtocolBundle)


@covergroup(contract="interface.mixed/v1")
class InterfaceCoverage(SignalCoverGroup[ProtocolBundle]):
    request_valid = Level(source=pins.signal(lambda p: p.request.valid, value_type=bool))
    response_ready = Level(source=pins.signal(lambda p: p.response.ready, value_type=bool))
    responses = Responses()
    valid_ready = Cross(request_valid, response_ready)


class PythonRequests(PatternCoverPoint[ProtocolBundle]):
    any_tag = python_request
    large_tag = Bin.pattern(python_request.with_args(minimum_tag=2))


@covergroup(contract="interface.mixed-with-python/v1")
class InterfaceWithPython(InterfaceCoverage):
    python_requests = PythonRequests()


# Bind InterfaceCoverage to use native execution in auto mode.
# Bind InterfaceWithPython to use the existing Python evaluator for the whole group:
# coverage.bind(pins=dut_bundle, sample=RisingEdge(clock), strategy="auto")
# The report records sampling_backend and the reason for Python sampling.
