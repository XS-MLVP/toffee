"""Transaction deadlines on real XClock phases, without a hardware protocol."""

import asyncio

import pytest

from toffee import (
    AsyncDriver, ClockCycles, Execution, RisingEdge, Scoreboard,
    ScoreboardTimeoutError, Transfer, TransferState, XCommClockBackend,
)

xspcomm = pytest.importorskip("xspcomm")


class Input(AsyncDriver[int, int]):
    def __init__(self, clock):
        self.clock = clock
        self.data = xspcomm.XData(8, xspcomm.XData.InOut)
        super().__init__((self.data,), name="native-input")

    async def _drive_one(self, request):
        self.data.Set(request)
        return await ClockCycles(self.clock, 1)


class Observations:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.closed = False

    def start(self, execution):
        return self

    async def recv(self):
        return await self.queue.get()

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize("latency", [0, 1, 3])
@pytest.mark.parametrize("observer_first", [False, True])
async def test_native_deadline_includes_response_phase(latency, observer_first):
    clock = xspcomm.XClock(lambda _: 0)
    backend = XCommClockBackend(clock)
    monitor = Observations()
    try:
        async with Execution(backend) as execution:
            async with Input(clock) as driver:
                async with Scoreboard("native").bind(
                    execution, driver=driver, monitor=monitor, clock=clock,
                    response_timeout_cycles=max(1, latency), latency_cycles=latency,
                ) as board:
                    async def respond():
                        for _ in range(latency + 1):
                            event = await RisingEdge(clock)
                        await asyncio.sleep(0)
                        monitor.queue.put_nowait(Transfer(event, 42))

                    async with execution.paused():
                        response = None
                        if observer_first:
                            response = asyncio.create_task(respond())
                            await asyncio.sleep(0)
                        transfer = board.submit(7, expected=42)
                        if response is None:
                            response = asyncio.create_task(respond())
                        await asyncio.sleep(0)
                    try:
                        async with asyncio.timeout(2):
                            assert await transfer == 42
                            await response
                            await board.finish(timeout_cycles=10, observe_cycles=1)
                    finally:
                        response.cancel()
                        await asyncio.gather(response, return_exceptions=True)
                    assert transfer.completed_event.tick - transfer.accepted_event.tick == 2 * latency
            assert not execution.reactor._drive_owners
            assert backend.watcher_count == 0
        assert monitor.closed
    finally:
        backend.close()


@pytest.mark.asyncio
async def test_native_missing_response_fails_and_cleans_up():
    clock = xspcomm.XClock(lambda _: 0)
    backend = XCommClockBackend(clock)
    try:
        async with Execution(backend) as execution:
            async with Input(clock) as driver:
                async with Scoreboard("missing-native").bind(
                    execution, driver=driver, monitor=Observations(), clock=clock,
                    response_timeout_cycles=2,
                ) as board:
                    transfer = board.submit(7, expected=42)
                    with pytest.raises(ScoreboardTimeoutError, match="response deadline"):
                        async with asyncio.timeout(2):
                            await transfer
                    assert transfer.state is TransferState.FAILED
            assert backend.watcher_count == 0
            assert not execution.reactor._drive_owners
    finally:
        backend.close()
