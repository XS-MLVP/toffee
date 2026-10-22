from __future__ import annotations

import asyncio

import pytest

from toffee import (
    Bundle,
    ClockCycles,
    Execution,
    MemoryBackend,
    SyncDriver,
    SyncSingleCycleDriver,
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


@pytest.mark.asyncio
async def test_sync_single_cycle_driver_accepts_scalar_and_restores_idle():
    clock = object()
    data = Signal()
    samples: list[int] = []

    def on_phase(phase: XPhase, tick: int) -> None:
        if phase is XPhase.RISING_STABLE:
            samples.append(data.value)

    async with Execution(MemoryBackend(clock, on_phase=on_phase)):
        driver = SyncSingleCycleDriver(clock, data, idle=0, name="scalar")
        event = await driver.send(7)
        assert event.tick == 2
        assert data.value == 0
        driver.close()

    assert samples == [7]


@pytest.mark.asyncio
async def test_sync_single_cycle_driver_accepts_bundle_without_binding_type():
    clock = object()
    data = Signal()
    async with Execution(MemoryBackend(clock)):
        driver = SyncSingleCycleDriver(
            clock,
            Bundle(data=data),
            idle={"data": 0},
            encoder=lambda request: {"data": request},
            name="bundle",
        )
        assert (await driver.send(0x5A)).tick == 2
        assert data.value == 0
        driver.close()


class TwoStageDriver(SyncDriver[int]):
    def __init__(self, clock: object, *, max_active: int) -> None:
        self.clock = clock
        self.address = Signal()
        self.payload = Signal()
        self.trace: list[tuple[str, int]] = []
        super().__init__(
            (self.address, self.payload),
            name="two_stage",
            max_active=max_active,
        )

    async def _drive_one(self, request: int):
        async with self.resource_lock("address"):
            self.trace.append(("address", request))
            self.address.Set(request)
            await ClockCycles(self.clock, 1)
        async with self.resource_lock("payload"):
            self.trace.append(("payload", request))
            self.payload.Set(request)
            event = await ClockCycles(self.clock, 1)
        self.trace.append(("accepted", request))
        return event


@pytest.mark.asyncio
@pytest.mark.parametrize("max_active,overlap", [(1, False), (2, True)])
async def test_sync_multicycle_driver_can_forbid_or_allow_overlap(
    max_active: int, overlap: bool
):
    clock = object()
    async with Execution(MemoryBackend(clock)):
        driver = TwoStageDriver(clock, max_active=max_active)
        first = asyncio.create_task(driver.send(1))
        second = asyncio.create_task(driver.send(2))
        first_event, second_event = await first, await second
        assert first_event.tick == 4
        assert second_event.tick > first_event.tick
        assert (
            driver.trace.index(("address", 2))
            < driver.trace.index(("accepted", 1))
        ) is overlap
        driver.close()
