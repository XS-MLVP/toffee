"""A runnable transaction lifecycle example without RTL or a bus protocol.

The in-memory responder doubles each accepted integer one cycle later. Replace
the input action and observation source with project-specific code in a DUT test.
"""

import asyncio

from toffee import (
    AsyncDriver, ClockCycles, Execution, MemoryBackend, Scoreboard,
    RisingEdge, SamplingMonitor, XPhase,
)


class Signal:
    def __init__(self):
        self.value = 0


class Input(AsyncDriver[int, int]):
    def __init__(self, clock, responses):
        self.clock = clock
        self.data = Signal()
        self.responses = responses
        super().__init__((self.data,), name="input")

    async def _drive_one(self, request):
        self.data.value = request
        accepted = await ClockCycles(self.clock, 1)
        self.responses[accepted.tick + 2] = request * 2
        return accepted


async def main():
    clock = object()
    responses = {}
    output = Signal()
    monitor = SamplingMonitor(RisingEdge(clock), capture=lambda _: output.value)

    def sample(phase, tick):
        if phase is XPhase.RISING_STABLE and tick in responses:
            output.value = responses.pop(tick)

    backend = MemoryBackend(clock, on_phase=sample)
    try:
        async with (
            Execution(backend) as execution,
            Input(clock, responses) as driver,
            Scoreboard("double", expected=lambda request, _: request * 2).bind(
                execution, driver=driver, monitor=monitor, clock=clock,
                response_timeout_cycles=4, latency_cycles=1, sampled=True,
            ) as scoreboard,
        ):
            for value in range(8):
                scoreboard.submit(value)
            status = await scoreboard.finish(timeout_cycles=20, observe_cycles=1)
            print(f"checked={status.checked}, passed={status.passed}, completed={status.completed}")
    finally:
        backend.close()


if __name__ == "__main__":
    asyncio.run(main())
