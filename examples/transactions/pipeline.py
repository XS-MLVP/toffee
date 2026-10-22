"""Protocol-independent pipeline examples; run with --help for individual cases.

The toy model samples positive request IDs on rising edges; zero means idle in
this example only. Its timing is deliberately explicit, not inferred by a Driver.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from dataclasses import dataclass

from toffee import (
    AsyncDriver, ClockCycles, Execution, MemoryBackend, Scoreboard,
    Transfer, XCommClockBackend, XEvent, XEventKind, XPhase,
)


class Signal:
    def __init__(self):
        self.value = 0

    def Set(self, value):
        self.value = value

    def U(self):
        return self.value


@dataclass(frozen=True)
class Response:
    tag: int
    value: int


class OutputMonitor:
    def __init__(self):
        self.queue = asyncio.Queue(maxsize=16)
        self.closed = False

    def start(self, execution):
        self.closed = False
        return self

    async def recv(self):
        return await self.queue.get()

    async def aclose(self):
        self.closed = True


class ToyPipeline:
    """A local DUT substitute, shared by the examples and their native tests."""

    def __init__(
        self, *, backend="memory", staged=False, respond=True,
        delays=None, drop=(),
    ):
        self.staged = staged
        self.second_cycles = 3
        self._second_value = 0
        self._second_samples = 0
        self.respond = respond
        self.delays = {1: 3, 2: 3, 3: 3} if delays is None else delays
        self.drop = set(drop)
        self.monitor = OutputMonitor()
        self.samples = []  # (half-tick, stage 1 value, stage 2 value)
        self.accepted = []  # (request ID, sampled acceptance half-tick)
        self.outputs = []  # (Response, sampled response half-tick)
        self.scheduled = defaultdict(list)
        self.native = backend == "native"
        if self.native:
            import xspcomm

            self.clock = xspcomm.XClock(lambda _: 0)
            self.first = xspcomm.XData(32, xspcomm.XData.InOut)
            self.second = xspcomm.XData(32, xspcomm.XData.InOut)
            self.backend = XCommClockBackend(self.clock)
            self.clock.StepRis(self._sample_native)
        elif backend == "memory":
            self.clock = object()
            self.first, self.second = Signal(), Signal()
            self.backend = MemoryBackend(self.clock, on_phase=self.sample)
        else:
            raise ValueError(f"unknown backend: {backend}")

    def _sample_native(self, cycle):
        # A fresh default XClock reaches rising-stable at half-tick 2 * cycle.
        # The callback runs before native trigger sampling, without an await.
        self.sample(XPhase.RISING_STABLE, cycle * 2)

    def sample(self, phase, tick):
        if phase is not XPhase.RISING_STABLE:
            return
        first, second = self.first.U(), self.second.U()
        self.samples.append((tick, first, second))
        request = second if self.staged else first
        if self.staged:
            if second != self._second_value or not second:
                self._second_samples = 0
            self._second_value = second
            if second:
                self._second_samples += 1
                if self._second_samples == self.second_cycles:
                    self._second_samples = 0
                else:
                    request = 0
        if request:
            self.accepted.append((request, tick))
            if self.respond and request not in self.drop:
                response = Response(request, request * 10)
                self.scheduled[tick + 2 * self.delays[request]].append(response)
        for response in self.scheduled.pop(tick, ()):
            event = XEvent(tick, tick, phase, XEventKind.CLOCK_RISE)
            self.outputs.append((response, tick))
            self.monitor.queue.put_nowait(Transfer(event, response))

    def close(self):
        try:
            self.backend.close()
        finally:
            if self.native:
                self.clock.RemoveStepRisCbByDesc("_sample_native")


class Input(AsyncDriver[int, Response]):
    """One input per cycle, independently of how long its response takes."""

    def __init__(self, toy, *, capacity=8):
        self.clock, self.data = toy.clock, toy.first
        super().__init__((self.data,), name="input", capacity=capacity, max_active=1)

    async def _drive_one(self, request):
        self.data.Set(request)
        try:
            return await ClockCycles(self.clock, 1)
        finally:
            self.data.Set(0)


async def drive_stages(driver, request):
    """Example-specific stage timing, shared by all submitted inputs."""
    async with driver.resource_lock("first"):
        driver.first.Set(request)
        try:
            await ClockCycles(driver.clock, 1)
        finally:
            driver.first.Set(0)
    async with driver.resource_lock("second"):
        driver.second.Set(request)
        try:
            return await ClockCycles(driver.clock, driver.second_cycles)
        finally:
            driver.second.Set(0)


class QueuedStages(AsyncDriver[int, Response]):
    def __init__(self, toy, *, max_active):
        self.clock, self.first, self.second = toy.clock, toy.first, toy.second
        self.second_cycles = toy.second_cycles
        super().__init__(
            (self.first, self.second), name="queued", max_active=max_active,
        )

    async def _drive_one(self, request):
        return await drive_stages(self, request)


async def run_responses(
    toy, *, association="fixed", requests=(1, 2, 3),
    response_timeout_cycles=6, capacity=8,
):
    """Submit all inputs before awaiting responses, then perform strict finish."""
    options = {}
    if association == "fixed":
        options["latency_cycles"] = 3
    elif association == "key":
        options.update(request_key=lambda request: request, response_key=lambda response: response.tag)
    elif association != "fifo":
        raise ValueError(f"unknown association: {association}")

    async with (
        Execution(toy.backend) as execution,
        Input(toy, capacity=capacity) as driver,
        Scoreboard("pipeline", expected=lambda request, _: Response(request, request * 10)).bind(
            execution, driver=driver, monitor=toy.monitor, clock=toy.clock,
            response_timeout_cycles=response_timeout_cycles, **options,
        ) as board,
    ):
        transfers = [board.submit(request) for request in requests]
        status = await board.finish(timeout_cycles=30, observe_cycles=1)
        assert status.completed == status.checked == len(requests)
        # Already complete: this does not serialize input generation.
        values = [await transfer for transfer in transfers]
        assert values == [Response(request, request * 10) for request in requests]
        accepted = [(t.request, t.accepted_event.tick) for t in transfers]
        assert accepted == toy.accepted
        return transfers


async def run_stages(toy, *, max_active, wait_each=False, requests=(1, 2, 3)):
    """Compare input-stage scheduling without requiring a response collector."""
    async with Execution(toy.backend), QueuedStages(toy, max_active=max_active) as driver:
        if wait_each:
            events = [await driver.send(request) for request in requests]
        else:
            inputs = [driver.send(request) for request in requests]
            # All inputs are already submitted; reading results in order does
            # not serialize their drive stages. The Driver owns their tasks.
            events = [await item for item in inputs]
        accepted = [(request, event.tick) for request, event in zip(requests, events)]
        assert accepted == toy.accepted
        return accepted


CASES = (
    "continuous", "fifo", "serial", "overlap", "send",
    "out-of-order", "missing", "late", "wrong-fifo", "capacity",
)
SUCCESS_CASES = CASES[:6]


async def run_case(case, backend):
    staged = case in {"serial", "overlap", "send"}
    delays = {1: 5, 2: 3, 3: 1} if case in {"out-of-order", "wrong-fifo"} else None
    if case == "fifo":
        delays = {1: 3, 2: 3, 3: 4}
    elif case == "late":
        delays = {1: 4}
    toy = ToyPipeline(
        backend=backend, staged=staged, respond=not staged,
        delays=delays, drop=(1,) if case == "missing" else (),
    )
    try:
        if staged:
            await run_stages(
                toy, max_active=1 if case == "serial" else 2,
                wait_each=case == "send",
            )
        else:
            association = "key" if case == "out-of-order" else "fixed"
            if case in {"fifo", "wrong-fifo", "missing"}:
                association = "fifo"
            await run_responses(
                toy, association=association,
                requests=(1,) if case in {"missing", "late"} else (1, 2, 3),
                response_timeout_cycles=2 if case == "missing" else 6,
                capacity=2 if case == "capacity" else 8,
            )
        print(f"{case} ({backend}): accepted={toy.accepted}")
        if staged:
            print(f"  samples (tick, first, second): {toy.samples}")
        else:
            print(f"  responses (tag, tick): {[(r.tag, tick) for r, tick in toy.outputs]}")
    finally:
        toy.close()


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=("all", *CASES), default="all")
    parser.add_argument("--backend", choices=("memory", "native"), default="memory")
    args = parser.parse_args()
    for case in SUCCESS_CASES if args.case == "all" else (args.case,):
        await run_case(case, args.backend)


if __name__ == "__main__":
    asyncio.run(main())
