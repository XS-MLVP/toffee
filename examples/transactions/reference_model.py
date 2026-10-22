"""A stateful reference model, interface Agent and out-of-order responses.

Run: python3 -m examples.transactions.reference_model [--backend native]
The local DUT substitute accumulates positive operands, one command per cycle.
No handshake, reset policy or response latency is inferred by the framework.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass

from toffee import (
    Agent, AsyncDriver, ClockCycles, Execution, MemoryBackend,
    PythonPredicateTrigger, RisingEdge, SamplingMonitor,
    XCommClockBackend, XPhase,
)


@dataclass(frozen=True)
class Command:
    tag: int
    operand: int


@dataclass(frozen=True)
class Result:
    tag: int
    total: int


class AccumulatorModel:
    """Executable functional specification, independent of DUT and asyncio."""

    def __init__(self, initial=0):
        self.total = initial

    def accept(self, command: Command) -> Result:
        self.total += command.operand
        return Result(command.tag, self.total)


class Signal:
    def __init__(self):
        self.value = 0

    def Set(self, value):
        self.value = value

    def U(self):
        return self.value


class AccumulatorDut:
    """Test substitute with explicit timing, separate from the reference model."""

    def __init__(self, backend, *, corrupt=False):
        self.native = backend == "native"
        self.corrupt = corrupt
        self.operands = []
        self.scheduled = {}
        self.output = None
        self.emitted_tags = []
        if self.native:
            import xspcomm

            self.clock = xspcomm.XClock(lambda _: 0)
            self.tag = xspcomm.XData(8, xspcomm.XData.InOut)
            self.operand = xspcomm.XData(16, xspcomm.XData.InOut)
            self.backend = XCommClockBackend(self.clock)
            self.clock.StepRis(self._native_sample)
        else:
            self.clock = object()
            self.tag, self.operand = Signal(), Signal()
            self.backend = MemoryBackend(self.clock, on_phase=self.sample)

    def _native_sample(self, cycle):
        self.sample(XPhase.RISING_STABLE, cycle * 2)

    def sample(self, phase, tick):
        if phase is not XPhase.RISING_STABLE:
            return
        self.output = None
        tag = self.tag.U()
        if tag:
            self.operands.append(self.operand.U())
            # Deliberately different implementation from the incremental model.
            total = sum(self.operands) + int(self.corrupt and tag == 2)
            latency = {1: 4, 2: 2, 3: 3}[tag]
            self.scheduled[tick + latency * 2] = Result(tag, total)
        if tick in self.scheduled:
            self.output = self.scheduled.pop(tick)
            self.emitted_tags.append(self.output.tag)

    def close(self):
        try:
            self.backend.close()
        finally:
            if self.native:
                self.clock.RemoveStepRisCbByDesc("_native_sample")


class Input(AsyncDriver[Command, Result]):
    def __init__(self, dut):
        self.dut = dut
        super().__init__((dut.tag, dut.operand), name="accumulator-input")

    async def _drive_one(self, command):
        self.dut.tag.Set(command.tag)
        self.dut.operand.Set(command.operand)
        try:
            return await ClockCycles(self.dut.clock, 1)
        finally:
            self.dut.tag.Set(0)
            self.dut.operand.Set(0)


class AccumulatorAgent(Agent[Input]):
    def __init__(self, dut, *, response_timeout_cycles):
        monitor = SamplingMonitor(
            PythonPredicateTrigger(
                "response-present", lambda: dut.output is not None,
                sample=RisingEdge(dut.clock), mode="each_sample",
            ),
            capture=lambda event: dut.output,  # Result is immutable.
        )
        super().__init__(
            "accumulator", driver=Input(dut), monitors={"response": monitor},
            response_monitor="response", clock=dut.clock,
            response_timeout_cycles=response_timeout_cycles,
            request_key=lambda request: request.tag,
            response_key=lambda response: response.tag,
        )


async def run_example(backend="memory", *, corrupt=False, cancel_pending=False):
    model = AccumulatorModel()
    dut = AccumulatorDut(backend, corrupt=corrupt)
    try:
        agent = AccumulatorAgent(dut, response_timeout_cycles=5)
        agent.connect(model)
        async with Execution(dut.backend, agents=[agent]):
            transfers = [agent.submit(Command(tag, operand)) for tag, operand in ((1, 3), (2, 5), (3, 7))]
            assert model.total == 0  # Submission has not changed the model.
            if cancel_pending:
                transfers[-1].cancel()
                transfers.pop()
            await agent.finish(timeout_cycles=30, observe_cycles=1)
            results = [await transfer for transfer in transfers]
        return results, dut.emitted_tags, model.total
    finally:
        dut.close()


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("memory", "native"), default="memory")
    args = parser.parse_args()
    results, order, total = await run_example(args.backend)
    print(f"expected/actual: {results}; response order: {order}; model total: {total}")


if __name__ == "__main__":
    asyncio.run(main())
