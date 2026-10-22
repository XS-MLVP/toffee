"""Minimal Execution and sampled-trigger example using MemoryBackend."""

from __future__ import annotations

import asyncio

from toffee import Execution, MemoryBackend, RisingEdge, Value, xtrigger


class Signal:
    def __init__(self, value: int = 0) -> None:
        self.value = value


class Dut:
    def __init__(self) -> None:
        self.clk = object()
        self.valid = Signal()
        self.ready = Signal()


@xtrigger(sample=RisingEdge("clk"))
def handshake(dut: Dut):
    return dut.valid & dut.ready


async def main() -> None:
    dut = Dut()
    backend = MemoryBackend(dut.clk)
    try:
        async with Execution(
            backend,
            default_sample=RisingEdge(dut.clk),
        ):
            dut.valid.value = 1
            dut.ready.value = 1
            ready = await Value(dut.ready, 1)
            accepted = await handshake(dut)
            print(f"ready at {ready.tick}, handshake at {accepted.tick}")
    finally:
        backend.close()


if __name__ == "__main__":
    asyncio.run(main())
