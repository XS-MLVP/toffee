"""Capture immutable values at a sample phase, then handle them asynchronously."""

import asyncio
from types import SimpleNamespace

from toffee import Execution, MemoryBackend, RisingEdge, XPhase, on


async def main():
    clock = object()
    count = SimpleNamespace(value=0)
    snapshots = asyncio.Queue()

    def sample(phase, tick):
        if phase is XPhase.RISING_STABLE:
            count.value += 1

    @on(RisingEdge(clock), capture=lambda event: (event.tick, count.value))
    async def observe(snapshot):
        # Consume captured values; do not re-read the live counter here.
        snapshots.put_nowait(snapshot)

    backend = MemoryBackend(clock, on_phase=sample)
    try:
        async with Execution(backend) as execution:
            execution.subscribe(observe.bind())
            for expected in range(1, 4):
                tick, value = await snapshots.get()
                assert value == expected
                print(f"tick={tick}, count={value}")
    finally:
        backend.close()


if __name__ == "__main__":
    asyncio.run(main())
