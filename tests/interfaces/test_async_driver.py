from __future__ import annotations

import asyncio

import pytest

from toffee import (
    AsyncSingleCycleDriver,
    Bundle,
    ClockCycles,
    Execution,
    MemoryBackend,
    AsyncDriver,
    DriverIncompleteError,
    Scoreboard,
    Transfer,
    TransferState,
    XEvent,
    XEventKind,
    XPhase,
)


class Signal:
    def __init__(self, value: int = 0) -> None:
        self.value = value

    def Set(self, value: int) -> None:
        self.value = value

    def U(self) -> int:
        return self.value

    def W(self) -> int:
        return 8


class TwoStageDriver(AsyncDriver[int, int]):
    def __init__(
        self, clock: object, *, max_active: int, capacity: int = 64
    ) -> None:
        self.clock = clock
        self.address = Signal()
        self.payload = Signal()
        self.payload_valid = Signal()
        self.trace: list[tuple[str, int]] = []
        super().__init__(
            (self.address, self.payload, self.payload_valid),
            name="two_stage",
            max_active=max_active,
            capacity=capacity,
        )

    async def _drive_one(self, request: int):
        async with self.resource_lock("address"):
            self.trace.append(("address", request))
            self.address.Set(request)
            await ClockCycles(self.clock, 1)
        async with self.resource_lock("payload"):
            self.trace.append(("payload", request))
            self.payload.Set(request)
            self.payload_valid.Set(1)
            try:
                event = await ClockCycles(self.clock, 1)
            finally:
                self.payload_valid.Set(0)
        self.trace.append(("accepted", request))
        return event


@pytest.mark.asyncio
async def test_framework_single_cycle_driver_launches_adjacent_scalar_requests():
    clock = object()
    data = Signal()
    samples: list[tuple[int, int]] = []

    def on_phase(phase: XPhase, tick: int) -> None:
        if phase is XPhase.RISING_STABLE:
            samples.append((tick, data.value))

    async with Execution(MemoryBackend(clock, on_phase=on_phase)) as execution:
        driver = AsyncSingleCycleDriver(clock, data, idle=0, name="scalar").start(
            execution
        )
        transfers = [driver.submit(value) for value in (1, 2, 3)]
        accepted = [await driver.recv_accepted() for _ in transfers]
        assert accepted == transfers
        assert [item.accepted_event.tick for item in accepted] == [2, 4, 6]
        await ClockCycles(clock, 1)
        for transfer in accepted:
            transfer.complete(transfer.accepted_event, transfer.request)
        await driver.aclose()

    assert samples[:4] == [(2, 1), (4, 2), (6, 3), (8, 0)]


@pytest.mark.asyncio
async def test_framework_single_cycle_driver_accepts_bundle_without_binding_type():
    clock = object()
    data = Signal()
    bits = Bundle(data=data)
    async with Execution(MemoryBackend(clock)) as execution:
        driver = AsyncSingleCycleDriver(
            clock,
            bits,
            idle={"data": 0},
            encoder=lambda value: {"data": value},
            name="bundle",
        ).start(execution)
        accepted = await driver.send(0x5A)
        assert accepted.tick == 2
        assert data.value == 0
        await driver.aclose()


@pytest.mark.asyncio
async def test_async_template_also_supports_single_cycle_input():
    class OneCycleDriver(AsyncDriver[int, int]):
        def __init__(self, clock: object) -> None:
            self.clock = clock
            self.data = Signal()
            super().__init__((self.data,), name="one_cycle")

        async def _drive_one(self, request: int):
            self.data.Set(request)
            return await ClockCycles(self.clock, 1)

    clock = object()
    async with Execution(MemoryBackend(clock)) as execution:
        driver = OneCycleDriver(clock).start(execution)
        first, second = driver.submit(1), driver.submit(2)
        accepted = [await driver.recv_accepted() for _ in range(2)]
        assert accepted == [first, second]
        assert [item.accepted_event.tick for item in accepted] == [2, 4]
        for transfer in accepted:
            transfer.complete(transfer.accepted_event, transfer.request)
        await driver.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("max_active,overlap", [(1, False), (2, True)])
async def test_multicycle_driver_can_forbid_or_allow_stage_overlap(
    max_active: int, overlap: bool
) -> None:
    clock = object()
    driver = TwoStageDriver(clock, max_active=max_active)
    async with Execution(MemoryBackend(clock)) as execution:
        driver.start(execution)
        first, second = driver.submit(1), driver.submit(2)
        assert first.state is second.state is TransferState.PENDING
        accepted = [await driver.recv_accepted() for _ in range(2)]
        assert accepted == [first, second]
        assert all(item.state is TransferState.PROCESSING for item in accepted)
        assert (
            driver.trace.index(("address", 2))
            < driver.trace.index(("accepted", 1))
        ) is overlap
        with pytest.raises(DriverIncompleteError):
            await driver.aclose()
    assert first.state is second.state is TransferState.FAILED


@pytest.mark.asyncio
async def test_async_driver_capacity_and_close_cancel_pending_transfers():
    clock = object()
    driver = TwoStageDriver(clock, max_active=1)
    driver.capacity = 1
    async with Execution(MemoryBackend(clock)) as execution:
        driver.start(execution)
        pending = driver.submit(1)
        with pytest.raises(RuntimeError, match="capacity is full"):
            driver.submit(2)
        await driver.aclose()
        assert pending.state is TransferState.CANCELLED
        with pytest.raises(RuntimeError, match="not started"):
            driver.submit(3)


@pytest.mark.asyncio
async def test_multistage_driver_integrates_with_async_scoreboard():
    class PayloadMonitor:
        def __init__(self) -> None:
            self.observed: asyncio.Queue[Transfer[int]] = asyncio.Queue()

        def start(self, execution: Execution) -> PayloadMonitor:
            return self

        async def recv(self) -> Transfer[int]:
            return await self.observed.get()

        async def aclose(self) -> None:
            pass

    clock = object()
    driver = TwoStageDriver(clock, max_active=2)
    monitor = PayloadMonitor()

    def on_phase(phase: XPhase, tick: int) -> None:
        if phase is XPhase.RISING_STABLE and driver.payload_valid.value:
            event = XEvent(tick, tick, XPhase.RISING_STABLE, XEventKind.CLOCK_RISE)
            monitor.observed.put_nowait(Transfer(event, driver.payload.value))

    async with Execution(MemoryBackend(clock, on_phase=on_phase)) as execution:
        driver.start(execution)
        scoreboard = Scoreboard("two_stage").bind(
            execution, driver=driver, monitor=monitor, clock=clock, response_timeout_cycles=20
        )
        first = scoreboard.submit(1, expected=1)
        second = scoreboard.submit(2, expected=2)
        await scoreboard.drain(timeout_cycles=30)
        assert first.state is second.state is TransferState.COMPLETED
        await scoreboard.aclose()
        await driver.aclose()


@pytest.mark.asyncio
async def test_async_driver_failure_reaches_transfers_and_accepted_stream():
    class FailingDriver(AsyncDriver[int, int]):
        def __init__(self, clock: object) -> None:
            self.clock = clock
            super().__init__((Signal(),), name="failing")

        async def _drive_one(self, request: int):
            await ClockCycles(self.clock, 1)
            raise ValueError(f"cannot drive {request}")

    clock = object()
    async with Execution(MemoryBackend(clock)) as execution:
        driver = FailingDriver(clock).start(execution)
        first, second = driver.submit(1), driver.submit(2)
        with pytest.raises(ValueError, match="cannot drive 1"):
            await driver.recv_accepted()
        assert first.state is second.state is TransferState.FAILED
        with pytest.raises(ValueError, match="cannot drive 1"):
            await first
        with pytest.raises(ValueError, match="cannot drive 1"):
            await second
        with pytest.raises(RuntimeError, match="has failed"):
            driver.submit(3)
        await driver.aclose()


@pytest.mark.asyncio
async def test_accepted_stream_overflow_is_explicit():
    clock = object()
    driver = TwoStageDriver(clock, max_active=1, capacity=1)
    async with Execution(MemoryBackend(clock)) as execution:
        driver.start(execution)
        first = driver.submit(1)
        await first.wait_processing()
        first.complete(first.accepted_event, 1)
        second = driver.submit(2)
        with pytest.raises(RuntimeError, match="accepted stream is full"):
            await second
        with pytest.raises(RuntimeError, match="accepted stream is full"):
            await driver.recv_accepted()
        await driver.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("max_active", [1, 2])
async def test_cancel_running_unaccepted_input_cleans_up_and_preserves_later_work(max_active):
    clock = object()
    driver = TwoStageDriver(clock, max_active=max_active)
    backend = MemoryBackend(clock)
    async with Execution(backend) as execution:
        async with execution.paused():
            driver.start(execution)
            first = driver.submit(1)
            await asyncio.sleep(0)
            second = driver.submit(2)
            first.cancel()
            await asyncio.sleep(0)
        accepted = await driver.recv_accepted()
        assert accepted is second
        second.complete(second.accepted_event, 2)
        await driver.aclose()
        assert not driver._tasks and not driver._pending
        assert not driver.has_outstanding
        assert not execution.reactor._drive_owners
    assert backend.watcher_count == 0


@pytest.mark.asyncio
async def test_cancel_before_worker_start_releases_task_entries():
    clock = object()
    driver = TwoStageDriver(clock, max_active=2)
    async with Execution(MemoryBackend(clock)) as execution:
        driver.start(execution)
        for _ in range(100):
            transfer = driver.submit(1)
            transfer.cancel()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not driver._tasks and not driver.has_outstanding
        await driver.aclose()


@pytest.mark.asyncio
async def test_send_releases_acceptance_only_work_without_a_response_collector():
    clock = object()
    async with Execution(MemoryBackend(clock)) as execution:
        driver = TwoStageDriver(clock, max_active=1, capacity=1).start(execution)
        for value in range(5):
            await driver.send(value)
        assert not driver.has_outstanding
        assert driver._accepted.empty()
        await driver.aclose()


@pytest.mark.asyncio
async def test_driver_preserves_body_failure_and_input_cleanup_failure():
    class FailingCleanup(TwoStageDriver):
        async def _drive_one(self, request):
            try:
                return await ClockCycles(self.clock, 10)
            finally:
                raise ValueError("input cleanup failure")
    clock = object()
    driver = FailingCleanup(clock, max_active=1)
    backend = MemoryBackend(clock)
    async with Execution(backend) as execution:
        with pytest.raises(BaseExceptionGroup) as caught:
            async with driver:
                driver.submit(1)
                await asyncio.sleep(0)
                raise LookupError("test body")
        assert any(isinstance(error, LookupError) for error in caught.value.exceptions)
        assert any(isinstance(error, ValueError) for error in caught.value.exceptions)
        assert not execution.reactor._drive_owners
    assert backend.watcher_count == 0
