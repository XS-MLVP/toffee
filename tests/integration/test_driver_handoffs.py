"""Lock/slot handoff precedes simulation advancement, including drive sampling."""

import asyncio

import pytest

from toffee import (
    ClockCycles, DriveStable, Execution, FallingEdge, MemoryBackend,
    SyncDriver, XCommClockBackend, on,
)


class Signal:
    def __init__(self):
        self.value = 0

    def Set(self, value):
        self.value = value

    def U(self):
        return self.value


@pytest.fixture(params=["memory", "native"])
def environment(request):
    if request.param == "native":
        xspcomm = pytest.importorskip("xspcomm")
        clock = xspcomm.XClock(lambda _: 0)
        data = xspcomm.XData(32, xspcomm.XData.InOut)
        backend = XCommClockBackend(clock)
    else:
        clock, data = object(), Signal()
        backend = MemoryBackend(clock)
    yield clock, data, backend
    try:
        assert backend.watcher_count == 0
        assert backend._owner is None
    finally:
        backend.close()


class Input(SyncDriver[int]):
    def __init__(self, clock, data, trace):
        self.clock, self.data, self.trace = clock, data, trace
        super().__init__((data,), name="input", max_active=1)

    async def _drive_one(self, request):
        self.trace.append(request)
        self.data.Set(request)
        return await FallingEdge(self.clock)


@pytest.mark.asyncio
async def test_chained_lock_handoffs_finish_before_drive_stable(environment):
    clock, data, backend = environment
    resumed, captured = [], []
    async with asyncio.timeout(2), Execution(backend) as execution:
        async with Input(clock, data, []) as driver:
            locks = [driver.resource_lock(index) for index in range(8)]
            for lock in locks:
                await lock.acquire()

            async def relay(index):
                async with locks[index]:
                    resumed.append((index, backend.tick))
                    data.Set(index + 1)
                    if index + 1 < len(locks):
                        locks[index + 1].release()

            @on(DriveStable(clock), capture=lambda event: (event.tick, data.U()))
            async def observe(snapshot):
                captured.append(snapshot)

            async with asyncio.TaskGroup() as group:
                for index in range(len(locks)):
                    group.create_task(relay(index))
                # Setup only: ensure every contender is waiting before the edge.
                await asyncio.sleep(0)
                execution.subscribe(observe.bind())
                await FallingEdge(clock)
                locks[0].release()
                await ClockCycles(clock, 20)
            assert resumed == [(index, 1) for index in range(8)]
            assert captured[0] == (1, 8)
        assert not execution.reactor._drive_owners


@pytest.mark.asyncio
async def test_concurrency_slot_wakes_next_input_before_clock_batch(environment):
    clock, data, backend = environment
    entered = []
    async with asyncio.timeout(2), Execution(backend) as execution:
        async with Input(clock, data, entered) as driver:
            async with asyncio.TaskGroup() as group:
                async with execution.paused():
                    calls = [group.create_task(driver.send(value)) for value in (1, 2, 3)]
                    await asyncio.sleep(0)
                # Independently armed long wait would otherwise jump over handoffs.
                await ClockCycles(clock, 100)
            assert [call.result().tick for call in calls] == [1, 3, 5]
            assert entered == [1, 2, 3]
        assert not execution.reactor._drive_owners


@pytest.mark.asyncio
@pytest.mark.parametrize("when", ["waiting", "granted"])
async def test_cancelled_waiter_passes_permit_to_next_without_delaying_time(environment, when):
    clock, data, backend = environment
    acquired = []
    async with asyncio.timeout(2), Execution(backend) as execution:
        async with Input(clock, data, []) as driver:
            lock = driver.resource_lock("stage")
            await lock.acquire()

            async def contender(label):
                async with lock:
                    acquired.append((label, backend.tick))

            first = asyncio.create_task(contender("cancelled"))
            second = asyncio.create_task(contender("next"))
            try:
                await asyncio.sleep(0)
                await FallingEdge(clock)
                if when == "waiting":
                    first.cancel()
                lock.release()
                if when == "granted":
                    first.cancel()
                await ClockCycles(clock, 10)
                with pytest.raises(asyncio.CancelledError):
                    await first
                await second
                assert acquired == [("next", 1)]
                assert not lock.locked()
            finally:
                first.cancel()
                second.cancel()
                await asyncio.gather(first, second, return_exceptions=True)


@pytest.mark.asyncio
async def test_reacquisition_cannot_overtake_queued_waiter(environment):
    clock, data, backend = environment
    order = []
    async with asyncio.timeout(2), Execution(backend):
        async with Input(clock, data, []) as driver:
            lock = driver.resource_lock("stage")
            assert isinstance(lock, asyncio.Lock)
            assert lock is driver.resource_lock("stage")
            await lock.acquire()

            async def contender(label):
                async with lock:
                    order.append(label)

            async with asyncio.TaskGroup() as group:
                group.create_task(contender("waiting"))
                await asyncio.sleep(0)
                lock.release()
                await contender("reacquire")
            assert order == ["waiting", "reacquire"]
            with pytest.raises(RuntimeError, match="not acquired"):
                lock.release()


@pytest.mark.asyncio
@pytest.mark.parametrize("host_activity", ["waiting", "ready"])
async def test_external_await_after_handoff_does_not_freeze_simulation(environment, host_activity):
    clock, data, backend = environment
    external = asyncio.Event()

    async def host_work():
        if host_activity == "waiting":
            await external.wait()
        else:
            while True:
                await asyncio.sleep(0)

    host = asyncio.create_task(host_work())
    try:
        async with asyncio.timeout(2), Execution(backend) as execution:
            async with Input(clock, data, []) as driver:
                lock = driver.resource_lock("stage")
                await lock.acquire()
                started = []

                async def contender():
                    async with lock:
                        started.append(backend.tick)
                        await external.wait()

                task = asyncio.create_task(contender())
                try:
                    await asyncio.sleep(0)
                    await FallingEdge(clock)
                    lock.release()
                    await ClockCycles(clock, 10)
                    assert started == [1]
                    assert backend.tick == 20
                    assert not task.done() and not host.done()
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
        assert not host.done()
    finally:
        host.cancel()
        await asyncio.gather(host, return_exceptions=True)


@pytest.mark.asyncio
async def test_failure_after_acquiring_lock_releases_it_to_next_waiter(environment):
    clock, data, backend = environment
    async with asyncio.timeout(2), Execution(backend) as execution:
        async with Input(clock, data, []) as driver:
            lock = driver.resource_lock("stage")
            await lock.acquire()
            resumed = []

            async def broken():
                async with lock:
                    raise ValueError("stage failed")

            async def next_request():
                async with lock:
                    resumed.append(backend.tick)

            failed = asyncio.create_task(broken())
            following = asyncio.create_task(next_request())
            try:
                await asyncio.sleep(0)
                await FallingEdge(clock)
                lock.release()
                await ClockCycles(clock, 10)
                with pytest.raises(ValueError, match="stage failed"):
                    await failed
                await following
                assert resumed == [1]
                assert not lock.locked()
            finally:
                failed.cancel()
                following.cancel()
                await asyncio.gather(failed, following, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancel_during_pause_entry_does_not_leave_pump_paused(environment):
    clock, _, backend = environment
    async with asyncio.timeout(2), Execution(backend) as execution:
        async def enter_pause():
            async with execution.paused():
                pytest.fail("cancelled pause should not enter the body")

        task = asyncio.create_task(enter_pause())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert execution._pause_depth == 0
        assert (await ClockCycles(clock, 2)).tick == 4
