import asyncio

import pytest

from toffee import (
    Bundle, ClockCycles, DriveStable, Execution, FallingEdge,
    RisingEdge, SamplingMonitor,  XPhase,
)


class Signal:
    def __init__(self, value=7):
        self.value = value

    def W(self):
        return 8

    def U(self):
        return self.value



@pytest.mark.asyncio
@pytest.mark.parametrize("trigger,phase", [
    (FallingEdge, XPhase.FALLING_STABLE),
    (RisingEdge, XPhase.RISING_STABLE),
    (DriveStable, XPhase.DRIVE_STABLE),
])
async def test_snapshot_and_trigger_event_preserved_across_batches(simulation, trigger, phase):
    clock, backend = simulation
    data = Signal()
    bundle = Bundle(value=data)
    captured_events = []

    def capture(event):
        captured_events.append(event)
        return bundle.sample()

    async with asyncio.timeout(2), Execution(backend) as execution:
        async with SamplingMonitor(trigger(clock), capture=capture).start(execution) as monitor:
            first = await monitor.recv()
            data.value = 9
            second = await monitor.recv()
            assert first.value.value.as_int() == 7
            assert second.value.value.as_int() == 9
            assert first.event is captured_events[0]
            assert second.event is captured_events[1]
            assert first.event.phase is second.event.phase is phase
            assert second.event.tick - first.event.tick == 2
    assert backend.watcher_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("observe", [True, False])
async def test_capture_failure_is_owned_and_reported_once(simulation, observe):
    clock, backend = simulation
    failure = ValueError("capture failed")

    def capture(event):
        raise failure

    async with asyncio.timeout(2), Execution(backend) as execution:
        monitor = SamplingMonitor(RisingEdge(clock), capture=capture).start(execution)
        if observe:
            with pytest.raises(ValueError) as caught:
                await monitor.recv()
        else:
            await ClockCycles(clock, 2)
            with pytest.raises(ValueError) as caught:
                await monitor.aclose()
        assert caught.value is failure
        await monitor.aclose()
        assert backend.watcher_count == 0


@pytest.mark.asyncio
async def test_async_capture_is_rejected_without_coroutine_leak(simulation):
    clock, backend = simulation

    async def capture(event):
        return event.tick

    with pytest.raises(TypeError, match="synchronous"):
        SamplingMonitor(RisingEdge(clock), capture=capture)
    async with asyncio.timeout(2), Execution(backend) as execution:
        monitor = SamplingMonitor(RisingEdge(clock), capture=lambda event: capture(event)).start(execution)
        with pytest.raises(TypeError, match="synchronous"):
            await monitor.recv()
        await monitor.aclose()
