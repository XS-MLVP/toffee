"""Clock advancement has one owner across native engines and Execution scopes."""

import pytest

from toffee import ClockCycles, Execution, FallingEdge, RunLimit, XCommClockBackend


@pytest.fixture
def clock():
    xspcomm = pytest.importorskip("xspcomm")
    return xspcomm.XClock(lambda _: 0)


@pytest.mark.asyncio
async def test_execution_owns_clock_even_when_idle_or_paused(clock):
    backend = XCommClockBackend(clock)
    assert not clock.IsControlled()
    clock.Step(2)
    initial_tick = clock.GetHalfTick()
    try:
        async with Execution(backend) as execution:
            assert clock.IsControlled()
            assert backend.tick == initial_tick
            for operation in (lambda: clock.Step(1), lambda: clock.Step(0),
                              lambda: clock.RawStep(1), clock.StepHalf, clock.Reset):
                with pytest.raises(RuntimeError, match="controlled by XEngine"):
                    operation()
            for operation in (lambda: clock.AStep(1), clock.ANext,
                              lambda: clock.ACondition(lambda: True),
                              lambda: clock.RunStep(1)):
                with pytest.raises(RuntimeError, match="controlled by XEngine"):
                    await operation()
            assert clock.GetHalfTick() == initial_tick
            async with execution.paused():
                with pytest.raises(RuntimeError, match="controlled by XEngine"):
                    clock.Step(1)
                assert clock.IsControlled()
            event = await ClockCycles(clock, 2)
            assert event.tick == initial_tick + 4
            with pytest.raises(RuntimeError, match="owning Execution"):
                backend.close()
        assert not clock.IsControlled()
        clock.Step(1)
        async with Execution(backend):
            await ClockCycles(clock, 1)
        assert not clock.IsControlled()
    finally:
        backend.close()


@pytest.mark.asyncio
async def test_distinct_backends_cannot_share_an_active_clock(clock):
    first, second = XCommClockBackend(clock), XCommClockBackend(clock)
    try:
        async with Execution(first):
            with pytest.raises(RuntimeError, match="another XEngine"):
                async with Execution(second):
                    pytest.fail("second Execution acquired an active clock")
            assert second._owner is None
            assert clock.IsControlled()
            second.close() # Closing an idle engine must leave the first lease alone.
            await ClockCycles(clock, 1)
        assert not clock.IsControlled()
    finally:
        first.close()
        second.close()


@pytest.mark.asyncio
async def test_exceptional_exit_releases_clock_without_advancing_its_phase(clock):
    backend = XCommClockBackend(clock)
    try:
        with pytest.raises(ValueError, match="test body"):
            async with Execution(backend):
                await FallingEdge(clock)
                tick = clock.GetHalfTick()
                raise ValueError("test body failed")
        assert backend._owner is None
        assert backend.watcher_count == 0
        assert not clock.IsControlled()
        assert clock.GetHalfTick() == tick
        assert not clock.IsAtCycleBoundary()
        clock.StepHalf() # Finish the remaining half before legacy full-cycle stepping.
        clock.Step(1)
        async with Execution(backend):
            await ClockCycles(clock, 1)
    finally:
        backend.close()


@pytest.mark.asyncio
async def test_failed_execution_start_releases_native_clock(clock, monkeypatch):
    def broken_reactor(*args, **kwargs):
        raise ValueError("reactor startup failed")

    backend = XCommClockBackend(clock)
    try:
        with monkeypatch.context() as patch:
            patch.setattr("toffee.execution.Reactor", broken_reactor)
            with pytest.raises(ValueError, match="reactor startup failed"):
                async with Execution(backend):
                    pytest.fail("startup unexpectedly succeeded")
        assert not clock.IsControlled()
        assert backend._owner is None
        clock.Step(1)
        async with Execution(backend):
            await ClockCycles(clock, 1)
    finally:
        backend.close()


def test_standalone_backend_claims_clock_on_first_use(clock):
    backend = XCommClockBackend(clock)
    other = XCommClockBackend(clock)
    assert not clock.IsControlled()
    try:
        handle = backend.arm(FallingEdge(clock))
        with pytest.raises(RuntimeError, match="controlled by XEngine"):
            clock.RawStep(1)
        with pytest.raises(RuntimeError, match="another XEngine"):
            other.acquire(object())
        with pytest.raises(RuntimeError, match="standalone registrations"):
            backend.acquire(object())
        assert backend.run_until(RunLimit(max_ticks=4)).advanced_ticks == 1
        backend.disarm(handle)
        assert clock.IsControlled()
        backend.close()
        assert not clock.IsControlled()
        clock.StepHalf()
        clock.Step(1)
    finally:
        backend.close()
        other.close()
