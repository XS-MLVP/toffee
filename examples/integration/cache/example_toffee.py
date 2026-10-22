import asyncio
import importlib

try:
    from UT_CacheSignalCFG import DUTCacheSignalCFG
except ImportError:
    try:
        from CacheSignalCFG import DUTCacheSignalCFG
    except ImportError:
        from __init__ import DUTCacheSignalCFG

from toffee import (
    ClockCycles,
    FallingEdge,
    RisingEdge,
    Execution,
    Value,
    XCommClockBackend,
    as_xdata,
    xtrigger,
)


@xtrigger(sample=RisingEdge("clock"))
def cache_accepts_request(dut):
    return dut.io_in_req_ready & ~dut.reset


def initialize_inputs(dut):
    """Drive every generated top-level input through its declared XData."""
    for signal in vars(dut).values():
        xdata = as_xdata(signal)
        if hasattr(xdata, "IsInIO") and xdata.IsInIO():
            xdata.AsImmWrite()
            xdata.Set(0)


async def test_cache():
    dut = DUTCacheSignalCFG()
    dut.InitClock("clock")
    initialize_inputs(dut)
    dut.reset.Set(1)

    clock = dut.GetXClock()
    xspcomm = importlib.import_module(type(clock).__module__)
    ready_data = as_xdata(dut.io_in_req_ready)
    assert (
        ready_data.GetBackendKind()
        == xspcomm.XDataBackendKind_MemDirect
    )

    backend = XCommClockBackend(clock)
    async with Execution(backend, max_batch_ticks=64):
        falling = await FallingEdge(dut.clock)
        assert falling.tick == 1

        await ClockCycles(dut.clock, 2)
        dut.reset.Set(0)
        await RisingEdge(dut.clock)

        async with asyncio.timeout(10):
            ready = await Value(
                dut.io_in_req_ready,
                1,
                sample=RisingEdge(dut.clock),
            )
            accepted = await cache_accepts_request(dut)

    assert ready.source is dut.io_in_req_ready
    assert accepted.value is True
    print(
        "Cache memory-direct toffee passed:",
        {
            "ready_tick": ready.tick,
            "xtrigger_tick": accepted.tick,
            "backend": "MemDirect",
        },
    )
    dut.Finish()


if __name__ == "__main__":
    asyncio.run(test_cache())
