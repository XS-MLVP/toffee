"""Lifecycle regressions; a trigger-only interface avoids protocol assumptions."""

import asyncio
from types import SimpleNamespace

import pytest

from toffee import (
    AsyncDriver, ClockCycles, Execution, FallingEdge,
    MonitorClosedError, ReadyValidMonitor, Role, SamplingMonitor, Scoreboard,
)


class Signal:
    def __init__(self):
        self.value = 7

    def W(self):
        return 8

    def U(self):
        return self.value



def make_monitor(clock, **kwargs):
    interface = SimpleNamespace(
        role=Role.MONITOR, clock=clock, bits=Signal(), fire=FallingEdge(clock),
    )
    return ReadyValidMonitor(interface, **kwargs)


@pytest.fixture(params=["protocol", "sampling"])
def lifecycle_monitor(request):
    if request.param == "protocol":
        return make_monitor
    return lambda clock, **kwargs: SamplingMonitor(
        FallingEdge(clock), capture=lambda event: event.tick, **kwargs,
    )


@pytest.mark.asyncio
async def test_close_wakes_all_receivers_and_rejects_restart(simulation, lifecycle_monitor):
    clock, backend = simulation
    monitor = lifecycle_monitor(clock)
    async with asyncio.timeout(2), Execution(backend) as execution:
        async with execution.paused():
            monitor.start(execution)
            waiting = [asyncio.create_task(monitor.recv()) for _ in range(3)]
            await asyncio.sleep(0)
            await monitor.aclose()
            results = await asyncio.gather(*waiting, return_exceptions=True)
            assert all(isinstance(error, MonitorClosedError) for error in results)
            with pytest.raises(RuntimeError, match="closed"):
                monitor.start(execution)
            await monitor.aclose()
        assert backend.watcher_count == 0


@pytest.mark.asyncio
async def test_close_discards_buffer_and_execution_close_wakes_receiver(simulation):
    clock, backend = simulation
    monitor = make_monitor(clock)
    async with asyncio.timeout(2), Execution(backend) as execution:
        monitor.start(execution)
        await ClockCycles(clock, 2)
        await monitor.aclose()
        with pytest.raises(RuntimeError, match="closed"):
            await monitor.recv()
        assert monitor._queue.empty()
        other = make_monitor(clock)
        async with execution.paused():
            other.start(execution)
            waiting = asyncio.create_task(other.recv())
            await asyncio.sleep(0)
    async with asyncio.timeout(2):
        result, = await asyncio.gather(waiting, return_exceptions=True)
    assert isinstance(result, MonitorClosedError)
    await other.aclose()
    assert backend.watcher_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["capture", "decoder"])
async def test_failure_reaches_recv_once_at_lifecycle_boundary(simulation, source, monkeypatch):
    clock, backend = simulation
    failure = ValueError(f"{source} failed")

    def broken(value):
        raise failure

    if source == "capture":
        monkeypatch.setattr("toffee.monitors.sample_data", broken)
    monitor = make_monitor(clock, decoder=broken if source == "decoder" else None)
    async with asyncio.timeout(2), Execution(backend) as execution:
        monitor.start(execution)
        waiting = [asyncio.create_task(monitor.recv()) for _ in range(2)]
        results = await asyncio.gather(*waiting, return_exceptions=True)
        assert all(error is failure for error in results)
        await monitor.aclose()  # Already observed: do not report it again.
        assert backend.watcher_count == 0
        # A handled component failure must not reappear on Execution exit.
        await ClockCycles(clock, 1)


@pytest.mark.asyncio
async def test_unobserved_failure_is_reported_by_close(simulation):
    clock, backend = simulation
    failure = ValueError("unobserved decoder failure")

    def broken(value):
        raise failure

    async with asyncio.timeout(2), Execution(backend) as execution:
        monitor = make_monitor(clock, decoder=broken).start(execution)
        await ClockCycles(clock, 3)
        with pytest.raises(ValueError) as caught:
            await monitor.aclose()
        assert caught.value is failure
        await monitor.aclose()
        assert backend.watcher_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("delivery", ["lossless", "latest"])
async def test_delivery_capacity_and_failure_priority(simulation, lifecycle_monitor, delivery):
    clock, backend = simulation
    async with asyncio.timeout(2), Execution(backend) as execution:
        monitor = lifecycle_monitor(clock, capacity=1, delivery=delivery).start(execution)
        await ClockCycles(clock, 4)
        if delivery == "lossless":
            with pytest.raises(RuntimeError, match="queue overflow"):
                await monitor.recv()
        else:
            observation = await monitor.recv()
            assert observation.event.tick >= 6
        await monitor.aclose()
        assert backend.watcher_count == 0


@pytest.mark.asyncio
async def test_context_cancellation_cleans_candidates_but_not_host_task(simulation):
    clock, backend = simulation
    ready = asyncio.Event()
    host_gate = asyncio.Event()
    host = asyncio.create_task(host_gate.wait())
    try:
        async with asyncio.timeout(2), Execution(backend) as execution:
            monitor = make_monitor(clock)

            async def owner():
                async with monitor.start(execution):
                    await FallingEdge(clock)
                    ready.set()
                    await asyncio.Event().wait()

            task = asyncio.create_task(owner())
            await ready.wait()
            task.cancel()
            result, = await asyncio.gather(task, return_exceptions=True)
            assert isinstance(result, asyncio.CancelledError)
            assert backend.watcher_count == 0
            assert not host.done()
    finally:
        host.cancel()
        await asyncio.gather(host, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancelling_one_receiver_does_not_stop_monitor(simulation, lifecycle_monitor):
    clock, backend = simulation
    async with asyncio.timeout(2), Execution(backend) as execution:
        async with lifecycle_monitor(clock).start(execution) as monitor:
            async with execution.paused():
                waiting = asyncio.create_task(monitor.recv())
                await asyncio.sleep(0)
                waiting.cancel()
                await asyncio.gather(waiting, return_exceptions=True)
            first = await monitor.recv()
            second = await monitor.recv()
            assert second.event.tick > first.event.tick
    assert backend.watcher_count == 0


@pytest.mark.asyncio
async def test_body_and_unobserved_failure_are_both_preserved(simulation):
    clock, backend = simulation
    failure = ValueError("decoder")
    body = AssertionError("body")

    def broken(value):
        raise failure

    async with asyncio.timeout(2), Execution(backend) as execution:
        with pytest.raises(ExceptionGroup) as caught:
            async with make_monitor(clock, decoder=broken).start(execution):
                await ClockCycles(clock, 3)
                raise body
        assert caught.value.exceptions == (body, failure)
        assert backend.watcher_count == 0


@pytest.mark.asyncio
async def test_monitor_failure_reaches_scoreboard_and_releases_driver(simulation):
    clock, backend = simulation
    failure = ValueError("monitor capture failed")

    def broken(value):
        raise failure

    class Input(AsyncDriver):
        def __init__(self):
            super().__init__((Signal(),), name="input")

        async def _drive_one(self, request):
            return await ClockCycles(clock, 1)

    async with asyncio.timeout(2), Execution(backend) as execution:
        async with Input() as driver:
            async with Scoreboard("failure").bind(
                execution, driver=driver, monitor=make_monitor(clock, decoder=broken),
                clock=clock, response_timeout_cycles=5,
            ) as board:
                transfer = board.submit(7, expected=7)
                with pytest.raises(ValueError) as caught:
                    await transfer
                assert caught.value is failure
        assert backend.watcher_count == 0
        assert not execution.reactor._drive_owners


@pytest.mark.asyncio
async def test_close_releases_remaining_watchers_after_disarm_error(simulation, monkeypatch):
    clock, backend = simulation
    cleanup_failure = RuntimeError("disarm diagnostic")
    body_failure = AssertionError("original assertion")
    original = backend.disarm
    calls = 0

    def disarm(handle):
        nonlocal calls
        original(handle)
        calls += 1
        if calls == 1:
            raise cleanup_failure

    async with asyncio.timeout(2), Execution(backend) as execution:
        async with execution.paused():
            monitor = make_monitor(clock).start(execution)
            # Capture creates a candidate watcher before the acceptance edge.
            monitor._subscription.capture(None)
            monkeypatch.setattr(backend, "disarm", disarm)
            with pytest.raises(ExceptionGroup) as caught:
                async with monitor:
                    raise body_failure
            assert caught.value.exceptions == (body_failure, cleanup_failure)
            assert backend.watcher_count == 0
            assert not monitor._candidates


@pytest.mark.asyncio
async def test_async_iteration_finishes_on_close(simulation, lifecycle_monitor):
    clock, backend = simulation
    async with Execution(backend) as execution:
        async with execution.paused():
            monitor = lifecycle_monitor(clock).start(execution)
            await monitor.aclose()
            with pytest.raises(StopAsyncIteration):
                await anext(monitor)
