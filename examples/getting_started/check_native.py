"""Check the installed xspcomm binding without constructing a DUT."""

import asyncio

import xspcomm

from toffee import ClockCycles, Execution, XCommClockBackend


async def main():
    print(f"xspcomm: {xspcomm.__file__}", flush=True)
    clock = xspcomm.XClock(lambda _: 0)
    backend = XCommClockBackend(clock)
    try:
        async with Execution(backend):
            event = await ClockCycles(clock, 1)
            print(f"native clock: tick={event.tick}, phase={event.phase.name}")
    finally:
        backend.close()


if __name__ == "__main__":
    asyncio.run(main())
