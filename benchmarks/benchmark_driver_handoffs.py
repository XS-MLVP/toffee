"""Measure Driver lock/slot handoffs using a no-op native XClock (no RTL)."""

import argparse
import asyncio
import json
import platform
import statistics
import time

import xspcomm

from toffee import ClockCycles, Execution, SyncDriver, XCommClockBackend


class Input(SyncDriver[int]):
    def __init__(self, clock, *, max_active):
        self.clock = clock
        self.first = xspcomm.XData(32, xspcomm.XData.InOut)
        self.second = xspcomm.XData(32, xspcomm.XData.InOut)
        super().__init__((self.first, self.second), name="benchmark", max_active=max_active)

    async def _drive_one(self, request):
        async with self.resource_lock("first"):
            self.first.Set(request)
            await ClockCycles(self.clock, 1)
        async with self.resource_lock("second"):
            self.second.Set(request)
            return await ClockCycles(self.clock, 3)


async def run(count, *, concurrent):
    clock = xspcomm.XClock(lambda _: 0)
    backend = XCommClockBackend(clock)
    try:
        async with Execution(backend), Input(clock, max_active=2 if concurrent else 1) as driver:
            if concurrent:
                async with asyncio.TaskGroup() as group:
                    for request in range(1, count + 1):
                        group.create_task(driver.send(request))
            else:
                for request in range(1, count + 1):
                    await driver.send(request)
        return backend.tick
    finally:
        backend.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requests", type=int, default=256)
    parser.add_argument("--repeats", type=int, default=7)
    args = parser.parse_args()
    if args.requests < 1 or args.repeats < 1:
        parser.error("--requests and --repeats must be positive")
    report = {"python": platform.python_version(), "requests": args.requests, "results": {}}
    for name, concurrent in (("uncontended", False), ("contended", True)):
        samples, ticks = [], []
        for _ in range(args.repeats):
            started = time.perf_counter_ns()
            ticks.append(asyncio.run(run(args.requests, concurrent=concurrent)))
            samples.append(time.perf_counter_ns() - started)
        report["results"][name] = {
            "median_ns_per_request": statistics.median(samples) / args.requests,
            "samples_ns": samples, "last_ticks": ticks,
        }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
