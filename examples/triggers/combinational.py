"""Clockless combinational verification uses the same async Execution scope."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from toffee import CombStable, Execution, MemoryBackend


async def main() -> None:
    dut = SimpleNamespace(a=0, b=0, sum=0)

    def evaluate() -> None:
        dut.sum = (dut.a + dut.b) & 255

    backend = MemoryBackend(on_comb=evaluate)
    try:
        async with Execution(backend):
            for a, b in ((1, 2), (255, 1), (0, 0)):
                dut.a, dut.b = a, b
                event = await CombStable()
                assert dut.sum == (a + b) & 255
                print(f"tick={event.tick} delta={event.delta}: {a} + {b} -> {dut.sum}")
    finally:
        backend.close()


if __name__ == "__main__":
    asyncio.run(main())
