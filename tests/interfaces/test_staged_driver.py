from __future__ import annotations

import asyncio

import pytest

from toffee import (
    AsyncMultiCycleDriver,
    ClockCycles,
    DriveStage,
    Execution,
    MemoryBackend,
    SyncMultiCycleDriver,
    TransferState,
    XPhase,
)


class Signal:
    def __init__(self) -> None:
        self.value = 0

    def Set(self, value: int) -> None:
        self.value = value

    def U(self) -> int:
        return self.value


def stages(first: Signal, second: Signal) -> tuple[DriveStage[int], ...]:
    return (
        DriveStage("first", first, 0, lambda request: request, cycles=1),
        DriveStage("second", second, 0, lambda request: request, cycles=2),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("max_active,overlap", [(1, False), (2, True)])
async def test_async_multicycle_driver_controls_stage_overlap(max_active: int, overlap: bool):
    clock = object()
    first, second = Signal(), Signal()
    first.Set(99)
    second.Set(99)
    sampled: list[tuple[int, int, int]] = []

    def capture(phase: XPhase, tick: int) -> None:
        if phase is XPhase.RISING_STABLE:
            sampled.append((tick, first.value, second.value))

    async with asyncio.timeout(2), Execution(MemoryBackend(clock, on_phase=capture)) as execution:
        async with AsyncMultiCycleDriver(
            clock, stages(first, second), max_active=max_active
        ) as driver:
            assert first.value == second.value == 0
            handles = [driver.send(request) for request in (1, 2, 3)]
            events = [await handle for handle in handles]
            assert [event.tick for event in events] == (
                [6, 10, 14] if overlap else [6, 12, 18]
            )
            assert not driver.has_outstanding
    assert ((4, 2, 1) in sampled) is overlap
    assert first.value == second.value == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("max_active,overlap", [(1, False), (2, True)])
async def test_sync_multicycle_driver_controls_stage_overlap(max_active: int, overlap: bool):
    clock = object()
    first, second = Signal(), Signal()
    first.Set(99)
    second.Set(99)
    sampled: list[tuple[int, int, int]] = []

    def capture(phase: XPhase, tick: int) -> None:
        if phase is XPhase.RISING_STABLE:
            sampled.append((tick, first.value, second.value))

    async with asyncio.timeout(2), Execution(MemoryBackend(clock, on_phase=capture)):
        async with SyncMultiCycleDriver(
            clock, stages(first, second), max_active=max_active
        ) as driver:
            assert first.value == second.value == 0
            calls = [asyncio.create_task(driver.send(request)) for request in (1, 2, 3)]
            events = await asyncio.gather(*calls)
            assert [event.tick for event in events] == (
                [6, 10, 14] if overlap else [6, 12, 18]
            )
    assert ((4, 2, 1) in sampled) is overlap
    assert first.value == second.value == 0


def test_multicycle_driver_rejects_invalid_stage_definitions():
    clock = object()
    first, second = Signal(), Signal()
    with pytest.raises(ValueError, match="at least two input cycles"):
        SyncMultiCycleDriver(clock, (DriveStage("only", first, 0, lambda x: x),))
    with pytest.raises(ValueError, match="duplicate stage name"):
        AsyncMultiCycleDriver(
            clock,
            (DriveStage("same", first, 0, lambda x: x),
             DriveStage("same", second, 0, lambda x: x)),
        )
    with pytest.raises(ValueError, match="multiple stages"):
        AsyncMultiCycleDriver(
            clock,
            (DriveStage("first", first, 0, lambda x: x),
             DriveStage("second", first, 0, lambda x: x)),
        )
    with pytest.raises(ValueError, match="positive integer"):
        SyncMultiCycleDriver(
            clock, (DriveStage("first", first, 0, lambda x: x, cycles=0),
                    DriveStage("second", second, 0, lambda x: x)),
        )


@pytest.mark.asyncio
async def test_multicycle_stage_restores_idle_after_encoder_failure():
    clock = object()
    first, second = Signal(), Signal()

    def broken(_: int) -> int:
        raise ValueError("encode failure")

    driver = SyncMultiCycleDriver(
        clock,
        (DriveStage("first", first, 0, lambda x: x),
         DriveStage("second", second, 0, broken)),
    )
    async with Execution(MemoryBackend(clock)):
        async with driver:
            with pytest.raises(ValueError, match="encode failure"):
                await driver.send(7)
    assert first.value == second.value == 0


@pytest.mark.asyncio
async def test_async_multicycle_submit_uses_normal_accepted_stream():
    clock = object()
    first, second = Signal(), Signal()
    async with Execution(MemoryBackend(clock)) as execution:
        driver = AsyncMultiCycleDriver(clock, stages(first, second)).start(execution)
        transfer = driver.submit(7)
        assert await driver.recv_accepted() is transfer
        assert transfer.accepted_event.tick == 6
        transfer.complete(transfer.accepted_event, 70)
        assert await transfer == 70
        await driver.aclose()
    assert first.value == second.value == 0


@pytest.mark.asyncio
async def test_async_multicycle_cancel_releases_occupied_stage():
    clock = object()
    first, second = Signal(), Signal()
    async with Execution(MemoryBackend(clock)) as execution:
        driver = AsyncMultiCycleDriver(
            clock, stages(first, second), max_active=2
        ).start(execution)
        cancelled = driver.send(1)
        # The first request is in its second stage after one clock cycle.
        await ClockCycles(clock, 1)
        cancelled.cancel()
        assert cancelled.state is TransferState.CANCELLED
        accepted = await driver.send(2)
        assert accepted.tick > 2
        assert not driver.has_outstanding
        await driver.aclose()
    assert first.value == second.value == 0
