"""Complete xtrigger programs with shared execution and separate result bins."""
from __future__ import annotations

import argparse
import asyncio
import json

from xreactor import (
    ClockCycles, ConditionMode, Execution, FSM, IllegalPolicy, Hold, RisingEdge,
    Sequence, State, Wait, Within, xtrigger,
)
from xreactor.declarative import Bin, SignalCoverGroup, TemporalCoverPoint, covergroup
from xreactor.ir import signal_expr
from examples.coverage.declarative_protocol import ProtocolBundle, RequestDriver, ToyResponder


@xtrigger(sample=RisingEdge("clock"), mode=ConditionMode.EACH_SAMPLE)
def accepted(pins: ProtocolBundle):
    return signal_expr(pins.request.valid) & signal_expr(pins.request.ready)


@xtrigger(sample=RisingEdge("clock"))
def roundtrip(pins: ProtocolBundle, *, tag: int = 1, maximum: int = 4):
    return Sequence(
        Wait(signal_expr(pins.request.valid) & signal_expr(pins.request.ready)
             & (signal_expr(pins.request.tag) == tag)),
        Within(1, maximum, signal_expr(pins.response.valid) & signal_expr(pins.response.ready)
               & (signal_expr(pins.response.tag) == tag)),
    )


@xtrigger(sample=RisingEdge("clock"))
def tag_two_then_ready(pins: ProtocolBundle):
    return Sequence(
        Wait(signal_expr(pins.request.valid) & signal_expr(pins.request.ready)
             & (signal_expr(pins.request.tag) == 2)),
        Within(1, 2, signal_expr(pins.response.valid) & (signal_expr(pins.response.tag) == 2)),
        Hold(signal_expr(pins.response.ready), cycles=2),
    )


@xtrigger(sample=RisingEdge("clock"))
def checked_tag_one(pins: ProtocolBundle):
    request = signal_expr(pins.request.valid) & signal_expr(pins.request.ready)
    response = signal_expr(pins.response.valid) & signal_expr(pins.response.ready)
    return FSM(start="idle", states={
        "idle": State().when(request & (signal_expr(pins.request.tag) == 1)).goto("pending"),
        "pending": State()
            .when(response & (signal_expr(pins.response.tag) == 1)
                  & (signal_expr(pins.response.data) == 10)).trigger("OK")
            .when(response & (signal_expr(pins.response.tag) == 1)).trigger("BAD_DATA"),
    })


class Requests(TemporalCoverPoint[ProtocolBundle]):
    handshake = accepted  # Direct declaration; no special coverage predicate.


class Completions(TemporalCoverPoint[ProtocolBundle]):
    tag_one = roundtrip  # Defaults: tag=1, maximum=4.
    tag_one_fast = Bin.pattern(roundtrip.with_args(tag=1, maximum=2))
    tag_two_ready = tag_two_then_ready
    # One FSM observation feeds both result bins; each retains its own counter.
    correct_data = Bin.pattern(checked_tag_one, terminals=("OK",))
    bad_data = Bin.illegal(Bin.pattern(checked_tag_one, terminals=("BAD_DATA",)))


@covergroup(schema_id="protocol.trigger-bins", contract="protocol.trigger-bins/v1")
class ProtocolCoverage(SignalCoverGroup[ProtocolBundle]):
    requests = Requests()
    completions = Completions()


async def run(*, engine="xcomm", strategy="native", wrong=()):
    toy = ToyResponder(engine=engine, wrong=wrong)
    try:
        coverage = ProtocolCoverage(instance="dut.protocol", illegal_policy=IllegalPolicy.RECORD)
        coverage.bind(pins=toy.pins, sample=RisingEdge(toy.clock), strategy=strategy)
        async with Execution(toy.backend, coverage=[coverage.runtime]):
            async with RequestDriver(toy.pins) as driver:
                for item in ((1, 0), (2, 1), (3, 0)):
                    await driver.send(item)
            await ClockCycles(toy.clock, 12 - toy.cycle)
        assert coverage.requests.count(Requests.handshake) == 3
        return coverage.report()
    finally:
        toy.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=("memory", "xcomm"), default="xcomm")
    parser.add_argument("--strategy", choices=("python", "native", "auto"), default="native")
    args = parser.parse_args()
    report = asyncio.run(run(engine=args.engine, strategy=args.strategy))
    print(json.dumps({"backend": report["sampling_backend"], "points": [
        {"name": point["name"], "counts": point["counts"]} for point in report["points"]]}, indent=2))


if __name__ == "__main__":
    main()
