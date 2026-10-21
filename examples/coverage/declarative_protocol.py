"""Pin-group coverage and correlated multi-cycle transactions on one protocol.

The local DUT accepts ready/valid requests and produces tagged responses with
variable latency. Coverage consumes actual stable pins and Monitor observations.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Protocol

from xreactor import (
    Bundle, BundleValue, ClockCycles, Execution, MemoryBackend, RisingEdge, SamplingMonitor,
    Sequence, SignalDriver, Transfer, Wait, Within, XCommClockBackend, XPhase, xtrigger,
)
from xreactor.declarative import (
    Bin, CoverGroup, CoverPoint, Cross, Fields, Iff, OverlapPolicy,
    covergroup, coverpoint, wire,
)
from xreactor.ir import signal_expr


class Pin(Protocol):
    """The small signal interface used by this example, including real XData."""
    def W(self) -> int: ...
    def U(self) -> int: ...
    def Set(self, value: int) -> None: ...


class RequestBundle(Bundle):
    """Project field annotations over the existing live Bundle."""

    valid: Pin
    ready: Pin
    tag: Pin
    opcode: Pin


class ResponseBundle(Bundle):
    valid: Pin
    ready: Pin
    tag: Pin
    data: Pin


@dataclass(frozen=True)
class RequestSnapshot:
    valid: bool
    ready: bool
    tag: int
    opcode: int


@dataclass(frozen=True)
class ResponseSnapshot:
    valid: bool
    ready: bool
    tag: int
    data: int


@dataclass(frozen=True)
class CycleSnapshot:
    request: RequestSnapshot
    response: ResponseSnapshot


class ProtocolBundle(Bundle):
    clock: object
    request: RequestBundle
    response: ResponseBundle

    def __init__(self, clock: object, request: RequestBundle, response: ResponseBundle):
        super().__init__(request=request, response=response)
        # A clock may be an XClock or a MemoryBackend token, not an XData leaf.
        self.clock = clock

    def capture(self, raw: BundleValue | None = None) -> CycleSnapshot:
        raw = self.sample() if raw is None else raw
        return CycleSnapshot(
            RequestSnapshot(bool(raw.request.valid.as_int()), bool(raw.request.ready.as_int()),
                            raw.request.tag.as_int(), raw.request.opcode.as_int()),
            ResponseSnapshot(bool(raw.response.valid.as_int()), bool(raw.response.ready.as_int()),
                             raw.response.tag.as_int(), raw.response.data.as_int()),
        )


@coverpoint(overlap=OverlapPolicy.ALLOW)
class ReadyPoint(CoverPoint[bool]):
    stalled = Bin.values(False)
    accepted = Bin.values(True)
    # Three consecutive valid cycles, counted once at the acceptance cycle.
    waited_two_then_accepted = Bin.transition(False, False, True)


class OpcodePoint(CoverPoint[int]):
    read = Bin.values(0)
    write = Bin.values(1)


cycle_fields = Fields(CycleSnapshot)


@covergroup(schema_id="protocol.request", contract="protocol.valid-request-cycles/v1",
            iff=Iff.truth(cycle_fields.select(lambda s: s.request.valid)))
class RequestCycleCoverage(CoverGroup[CycleSnapshot]):
    ready = ReadyPoint(source=cycle_fields.select(lambda s: s.request.ready))
    opcode = OpcodePoint(source=cycle_fields.select(lambda s: s.request.opcode))
    ready_opcode = Cross(ready, opcode)


class ResponseTagPoint(CoverPoint[int]):
    one = Bin.values(1)


class ResponseDataPoint(CoverPoint[int]):
    read_one = Bin.values(10)


@covergroup(schema_id="protocol.tag-one-roundtrip", contract="protocol.tag-one-roundtrip/v1")
class RoundTripCoverage(CoverGroup[CycleSnapshot]):
    tag = ResponseTagPoint(source=cycle_fields.select(lambda s: s.response.tag))
    data = ResponseDataPoint(source=cycle_fields.select(lambda s: s.response.data))
    tag_data = Cross(tag, data)


@xtrigger(sample=RisingEdge("clock"))
def tag_one_roundtrip(pins, *, maximum=4):
    """A bounded multi-cycle sample event, with explicit matching tag 1."""
    return Sequence(
        Wait(signal_expr(pins.request.valid) & signal_expr(pins.request.ready)
             & (signal_expr(pins.request.tag) == 1)),
        Within(1, maximum, signal_expr(pins.response.valid) & signal_expr(pins.response.ready)
               & (signal_expr(pins.response.tag) == 1)),
    )


def bind_pin_coverage(pins: ProtocolBundle, *, strategy="native", maximum=4):
    request = RequestCycleCoverage(instance="dut.request")
    request.bind(trigger=RisingEdge(pins.clock), strategy=strategy, fields=(
        wire(cycle_fields.select(lambda s: s.request.valid), pins.request.valid, source_id="dut.request.valid"),
        wire(cycle_fields.select(lambda s: s.request.ready), pins.request.ready, source_id="dut.request.ready"),
        wire(cycle_fields.select(lambda s: s.request.opcode), pins.request.opcode, source_id="dut.request.opcode"),
    ))
    completed = RoundTripCoverage(instance="dut.tag-one-roundtrip")
    completed.bind(trigger=tag_one_roundtrip(pins, maximum=maximum), strategy=strategy, fields=(
        wire(cycle_fields.select(lambda s: s.response.tag), pins.response.tag, source_id="dut.response.tag"),
        wire(cycle_fields.select(lambda s: s.response.data), pins.response.data, source_id="dut.response.data"),
    ))
    return request, completed


@dataclass(frozen=True)
class CompletedTransaction:
    tag: int
    opcode: int
    latency: int
    correct: bool
    out_of_order: bool
    accepted_tick: int
    response_tick: int


class LatencyPoint(CoverPoint[int]):
    one_cycle = Bin.values(1)
    two_or_three = Bin.range(2, 3)
    four_to_six = Bin.range(4, 6)


class OutcomePoint(CoverPoint[bool]):
    yes = Bin.values(True)
    no = Bin.values(False)


@covergroup(schema_id="protocol.transactions", contract="protocol.tag-correlated-responses/v1")
class TransactionCoverage(CoverGroup[CompletedTransaction]):
    opcode = OpcodePoint()
    latency = LatencyPoint()
    correct = OutcomePoint()
    out_of_order = OutcomePoint()
    opcode_latency = Cross(opcode, latency)


class TransactionCollector:
    """One consumer correlates IDs; Sequence alone cannot do dynamic ID matching."""
    def __init__(self):
        self.pending = {}
        self.completed: list[CompletedTransaction] = []
        self.last_tick = -1

    def observe(self, observation: Transfer[CycleSnapshot]) -> CompletedTransaction | None:
        event, snapshot = observation.event, observation.value
        if event.phase is not XPhase.RISING_STABLE or event.tick <= self.last_tick:
            raise ValueError("collector requires distinct increasing rising-stable observations")
        request, response = snapshot.request, snapshot.response
        request_fire = request.valid and request.ready
        response_fire = response.valid and response.ready
        completed = None
        if response_fire:
            if response.tag not in self.pending:
                raise ValueError(f"response for unknown tag {response.tag}")
            accepted_tick, opcode = self.pending[response.tag]
            latency_ticks = event.tick - accepted_tick
            if latency_ticks <= 0 or latency_ticks % 2:
                raise ValueError("response latency must be a positive number of full cycles")
            completed = CompletedTransaction(
                response.tag, opcode, latency_ticks // 2,
                response.data == response.tag * 10 + opcode,
                response.tag != next(iter(self.pending)), accepted_tick, event.tick,
            )
        if request_fire and request.tag in self.pending and not (response_fire and response.tag == request.tag):
            raise ValueError(f"duplicate accepted tag {request.tag}")
        self.last_tick = event.tick
        if completed is not None:
            del self.pending[response.tag]
            self.completed.append(completed)
        if request_fire:
            self.pending[request.tag] = (event.tick, request.opcode)
        return completed

    def finish(self):
        if self.pending:
            raise ValueError(f"responses missing for tags {sorted(self.pending)}")


class _Signal:
    def __init__(self, width):
        self.width, self.value = width, 0

    def W(self):
        return self.width

    def U(self):
        return self.value

    def Set(self, value):
        self.value = value


class ToyResponder:
    """Deterministic DUT substitute: two stalls, variable latency, reordered output."""
    def __init__(self, *, engine="xcomm", delays=None, drop=(), wrong=()):
        if engine not in ("memory", "xcomm"):
            raise ValueError("engine must be memory or xcomm")
        self.native = engine == "xcomm"
        if self.native:
            import xspcomm
            self.clock = xspcomm.XClock(lambda _: 0)
            signal = lambda width: xspcomm.XData(width, xspcomm.XData.InOut)
        else:
            self.clock = object()
            signal = _Signal
        self.pins = ProtocolBundle(
            self.clock,
            RequestBundle(valid=signal(1), ready=signal(1), tag=signal(8), opcode=signal(2)),
            ResponseBundle(valid=signal(1), ready=signal(1), tag=signal(8), data=signal(16)),
        )
        self.pins.response.ready.Set(1)
        self.delays = {1: 3, 2: 1, 3: 1} if delays is None else dict(delays)
        self.drop, self.wrong = set(drop), set(wrong)
        self.scheduled = []
        self.cycle = 0
        self.accepted = []
        if self.native:
            self.backend = XCommClockBackend(self.clock)
            self.clock.StepRis(self._evaluate_native)
        else:
            self.backend = MemoryBackend(self.clock, on_phase=self._evaluate_memory)

    def _evaluate_native(self, cycle):
        self._evaluate(cycle)

    def _evaluate_memory(self, phase, tick):
        if phase is XPhase.RISING_STABLE:
            self._evaluate(tick // 2)

    def _evaluate(self, cycle):
        self.cycle = cycle
        request, response = self.pins.request, self.pins.response
        request.ready.Set(int(cycle > 2))
        if request.valid.U() and request.ready.U():
            tag, opcode = request.tag.U(), request.opcode.U()
            self.accepted.append((tag, cycle))
            if tag not in self.drop:
                self.scheduled.append((cycle + self.delays[tag], tag, tag * 10 + opcode + int(tag in self.wrong)))
        due = next((index for index, item in enumerate(self.scheduled) if item[0] <= cycle), None)
        response.valid.Set(int(due is not None))
        if due is not None:
            _, tag, data = self.scheduled[due]
            response.tag.Set(tag)
            response.data.Set(data)
            if response.ready.U():
                self.scheduled.pop(due)

    def close(self):
        self.backend.close()
        if self.native:
            self.clock.RemoveStepRisCbByDesc("_evaluate_native")


class RequestDriver(SignalDriver[tuple[int, int]]):
    def __init__(self, pins: ProtocolBundle):
        self.pins = pins
        super().__init__((pins.request.valid, pins.request.tag, pins.request.opcode), name="request")

    async def send(self, transaction: tuple[int, int]):
        request = self.pins.request
        request.tag.Set(transaction[0])
        request.opcode.Set(transaction[1])
        request.valid.Set(1)
        try:
            while True:
                event = await ClockCycles(self.pins.clock, 1)
                if request.ready.U():
                    return event
        finally:
            request.valid.Set(0)


async def run(*, engine="xcomm", strategy="native", maximum=4, cycles=12, delays=None,
              drop=(), wrong=(), output: Path | None = None):
    if engine == "memory" and strategy == "native":
        raise ValueError("memory engine requires python coverage")
    if type(cycles) is not int or cycles < 1:
        raise ValueError("cycles must be a positive integer")
    toy = ToyResponder(engine=engine, delays=delays, drop=drop, wrong=wrong)
    collector = TransactionCollector()
    captures = []

    async def produce():
        async with RequestDriver(toy.pins) as driver:
            for transaction in ((1, 0), (2, 1), (3, 0)):
                await driver.send(transaction)
        if toy.cycle < cycles:
            await ClockCycles(toy.clock, cycles - toy.cycle)

    async def consume(monitor):
        for _ in range(cycles):
            observation = await monitor.recv()
            captures.append({"tick": observation.event.tick, **asdict(observation.value)})
            # This is the explicit transaction-to-coverage binding.
            completed = collector.observe(observation)
            if completed is not None:
                transactions.sample(completed)

    try:
        request, roundtrip = bind_pin_coverage(toy.pins, strategy=strategy, maximum=maximum)
        transactions = TransactionCoverage(instance="dut.transactions")
        async with Execution(toy.backend, coverage=[request.runtime, roundtrip.runtime]) as execution:
            live = toy.pins
            monitor = SamplingMonitor(RisingEdge(toy.clock), capture=lambda _: toy.pins.capture(live.sample()),
                                      capacity=cycles + 4).start(execution)
            async with monitor:
                async with asyncio.TaskGroup() as tasks:
                    tasks.create_task(produce())
                    tasks.create_task(consume(monitor))
        if captures[-1]["tick"] // 2 != toy.cycle:
            raise ValueError("observation horizon ended before the driver completed")
        collector.finish()
        assert toy.backend.watcher_count == 0
        if toy.native:
            assert toy.backend._engine.ActiveCount() == 0
        result = {"engine": engine, "strategy": strategy, "cycles": cycles,
                  "accepted": toy.accepted, "transactions": [asdict(item) for item in collector.completed],
                  "captures": captures, "request_coverage": request.report(),
                  "roundtrip_coverage": roundtrip.report(), "transaction_coverage": transactions.report()}
        if output:
            output.mkdir(parents=True, exist_ok=True)
            (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
            for name, coverage in (("request", request), ("roundtrip", roundtrip)):
                (output / f"{name}-binding.json").write_text(coverage.binding_contract_json() + "\n")
        return result
    finally:
        toy.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=("memory", "xcomm"), default="xcomm")
    parser.add_argument("--strategy", choices=("python", "native", "auto"), default="native")
    parser.add_argument("--maximum", type=int, default=4)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = asyncio.run(run(engine=args.engine, strategy=args.strategy, maximum=args.maximum, output=args.output))
    print(json.dumps({key: result[key] for key in ("engine", "strategy", "accepted", "transactions")}, indent=2))
    for key in ("request_coverage", "roundtrip_coverage", "transaction_coverage"):
        report = result[key]
        print(f"{key}: samples={report['samples']} backend={report['sampling_backend']}")
        for point in report["points"]:
            print(f"  {point['name']}: {point['counts']}")


if __name__ == "__main__":
    main()
