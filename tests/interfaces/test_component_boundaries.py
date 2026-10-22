"""Invalid composition and partial component lifecycles preserve ownership."""

import asyncio
from types import SimpleNamespace

import pytest

from toffee import (
    Agent, AsyncDriver, AsyncSingleCycleDriver, Bundle, ClockCycles, Execution,
    FallingEdge, MemoryBackend, MonitorClosedError, ReadyValidMonitor, Role,
    SamplingMonitor, SyncDriver, RisingEdge, ReadyValid, drive_ready_valid,
)


class Signal:
    def __init__(self):
        self.value = 0

    def W(self):
        return 8

    def Set(self, value):
        self.value = value


class Input(SyncDriver):
    def __init__(self, clock, *, failure=None, result=None, max_active=1):
        self.clock, self.failure, self.result = clock, failure, result
        super().__init__((Signal(),), name="input", max_active=max_active)

    async def _drive_one(self, request):
        if self.failure is not None:
            raise self.failure
        return self.result if self.result is not None else await ClockCycles(self.clock, 1)


class AsyncInput(AsyncDriver):
    def __init__(self, clock, **options):
        self.clock = clock
        super().__init__((Signal(),), name="async_input", **options)

    async def _drive_one(self, request):
        return await ClockCycles(self.clock, 1)


class DirectionalSignal(Signal):
    def __init__(self, direction):
        super().__init__()
        self.direction = direction

    def IsInIO(self):
        return self.direction == "in"

    def IsOutIO(self):
        return self.direction == "out"


def test_ready_valid_consumer_direction_and_role_views():
    clock = object()
    interface = ReadyValid(clock, DirectionalSignal("out"), DirectionalSignal("in"),
                           DirectionalSignal("out"), role=Role.CONSUMER)
    assert interface.as_role(Role.MONITOR).role is Role.MONITOR
    with pytest.raises(ValueError, match="peer-driven side"):
        ReadyValid(clock, DirectionalSignal("in"), DirectionalSignal("in"),
                   DirectionalSignal("out"), role=Role.CONSUMER)
    with pytest.raises(ValueError, match="name"):
        ReadyValid(clock, Signal(), Signal(), Signal(), name="")
    root = SimpleNamespace(valid=Signal(), ready=Signal(), bits=Signal())
    with pytest.raises(ValueError, match="aggregate signal-tree"):
        ReadyValid.bind_tree(root, {name: {"_": True} for name in ("valid", "ready", "bits")},
                             clock=clock, source_prefix="")


@pytest.mark.asyncio
async def test_ready_valid_drive_callback_requires_none_result():
    backend = MemoryBackend(object())
    valid, ready = Signal(), Signal()
    async with Execution(backend):
        with pytest.raises(TypeError, match="return None"):
            await drive_ready_valid(backend.clock, valid, ready, lambda: 1)
        assert valid.value == 0
        assert (await ClockCycles(backend.clock, 1)).tick == 2


@pytest.mark.asyncio
async def test_async_driver_rejects_synchronous_close_with_active_input():
    backend = MemoryBackend(object())
    async with Execution(backend):
        async with AsyncInput(backend.clock) as driver:
            transfer = driver.send(1)
            with pytest.raises(RuntimeError, match="await aclose"):
                driver.close()
            assert (await transfer).tick == 2


@pytest.mark.asyncio
async def test_failed_async_driver_cannot_restart_after_failure_is_observed():
    backend = MemoryBackend(object())
    failure = ValueError("input failed")

    class FailingInput(AsyncInput):
        async def _drive_one(self, request):
            raise failure

    async with Execution(backend):
        driver = FailingInput(backend.clock)
        async with driver:
            transfer = driver.send(1)
            with pytest.raises(ValueError) as caught:
                await transfer
            assert caught.value is failure
        with pytest.raises(RuntimeError, match="cannot be restarted"):
            await driver.__aenter__()


@pytest.mark.parametrize("options,message", [
    ({"name": ""}, "name"),
    ({"response_monitor": "missing"}, "owned monitor"),
    ({"response_monitor": "out", "driver": SimpleNamespace(__aenter__=lambda: None, __aexit__=lambda: None)}, "submit/recv_accepted"),
    ({"response_monitor": "out"}, "explicit clock"),
    ({"clock": object()}, "response options"),
])
def test_agent_response_configuration_is_explicit(options, message):
    clock = object()
    args = dict(name="port", driver=Input(clock), monitors={
        "out": SamplingMonitor(RisingEdge(clock), capture=lambda event: event.tick),
    })
    args.update(options)
    with pytest.raises((ValueError, TypeError), match=message):
        Agent(**args)


@pytest.mark.asyncio
async def test_unchecked_agent_rejects_reference_and_expected_result():
    backend = MemoryBackend(object())
    agent = Agent("input", driver=Input(backend.clock))
    with pytest.raises(RuntimeError, match="configured response monitor"):
        agent.connect(SimpleNamespace(accept=lambda request: request))
    with pytest.raises(RuntimeError, match="no response checker"):
        _ = agent.status
    async with Execution(backend, agents=[agent]):
        with pytest.raises(RuntimeError, match="expected requires"):
            agent.send(1, expected=1)
        assert (await agent.send(1)).tick == 2


@pytest.mark.asyncio
async def test_checked_sync_send_failure_propagates_and_releases_driver():
    backend = MemoryBackend(object())
    failure = ValueError("input rejected")
    driver = Input(backend.clock, failure=failure)
    monitor = SamplingMonitor(RisingEdge(backend.clock), capture=lambda event: event.tick)
    agent = Agent("checked", driver=driver, monitors={"out": monitor},
                  response_monitor="out", clock=backend.clock, response_timeout_cycles=2)
    async with Execution(backend, agents=[agent]):
        with pytest.raises(ValueError) as caught:
            await agent.send(1, expected=1)
        assert caught.value is failure
    assert backend.watcher_count == 0 and backend._owner is None
    async with Execution(backend), Input(backend.clock) as replacement:
        assert (await replacement.send(1)).tick == 2


@pytest.mark.asyncio
async def test_monitor_lifecycle_validation_and_iteration():
    backend = MemoryBackend(object())
    monitor = SamplingMonitor(FallingEdge(backend.clock), capture=lambda event: event.tick)
    for operation in (monitor.recv, monitor.__aenter__):
        with pytest.raises(RuntimeError, match="not started"):
            await operation()
    with pytest.raises(RuntimeError, match="active Execution"):
        monitor.start(Execution(backend))
    async with Execution(backend) as execution:
        monitor.start(execution)
        with pytest.raises(RuntimeError, match="already started"):
            monitor.start(execution)
        assert aiter(monitor) is monitor
        observation = await anext(monitor)
        assert observation.value == observation.event.tick == 1
        await monitor.aclose()
        with pytest.raises(StopAsyncIteration):
            await anext(monitor)
        with pytest.raises(MonitorClosedError):
            await monitor.__aenter__()
    assert backend.watcher_count == 0


@pytest.mark.parametrize("options,message", [
    ({"delivery": "drop"}, "delivery"),
    ({"capacity": 0}, "capacity"),
])
def test_monitor_configuration(options, message):
    with pytest.raises(ValueError, match=message):
        SamplingMonitor(FallingEdge(object()), capture=lambda event: 0, **options)


def test_protocol_monitor_requires_monitor_role():
    with pytest.raises(ValueError, match="role=MONITOR"):
        ReadyValidMonitor(SimpleNamespace(role=Role.PRODUCER))


@pytest.mark.parametrize("driver, options, message", [
    (AsyncInput, {"capacity": 0}, "capacity"),
    (AsyncInput, {"max_active": 0}, "max_active"),
    (Input, {"max_active": 0}, "max_active"),
])
def test_driver_configuration(driver, options, message):
    with pytest.raises(ValueError, match=message):
        driver(object(), **options)


@pytest.mark.asyncio
async def test_async_driver_requires_active_execution_and_can_close_without_work():
    backend = MemoryBackend(object())
    driver = AsyncInput(backend.clock)
    with pytest.raises(RuntimeError, match="active Execution"):
        driver.start(Execution(backend))
    async with Execution(backend) as execution:
        driver.start(execution)
        with pytest.raises(RuntimeError, match="already started"):
            driver.start(execution)
        driver.close()
        async with AsyncInput(backend.clock) as replacement:
            assert (await replacement.send(1)).tick == 2
    assert backend.watcher_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["start", "context"])
async def test_single_cycle_invalid_idle_releases_signal_ownership(entry):
    backend = MemoryBackend(object())
    signal = Signal()
    driver = AsyncSingleCycleDriver(backend.clock, Bundle(data=signal), idle={})
    async with Execution(backend) as execution:
        with pytest.raises(ValueError, match="shape mismatch"):
            if entry == "start":
                driver.start(execution)
            else:
                await driver.__aenter__()
        async with AsyncSingleCycleDriver(backend.clock, signal, idle=0) as replacement:
            assert (await replacement.send(7)).tick == 2
    assert signal.value == 0 and backend.watcher_count == 0


@pytest.mark.asyncio
async def test_driver_must_return_acceptance_event_and_sync_close_checks_active_work():
    backend = MemoryBackend(object())
    async with Execution(backend):
        async with Input(backend.clock, result=17) as invalid:
            with pytest.raises(TypeError, match="XEvent"):
                await invalid.send(1)
        driver = Input(backend.clock)
        started, gate = asyncio.Event(), asyncio.Event()

        async def held(request):
            started.set()
            await gate.wait()
            return await ClockCycles(backend.clock, 1)

        driver._drive_one = held
        async with driver:
            task = asyncio.create_task(driver.send(1))
            await started.wait()
            with pytest.raises(RuntimeError, match="active input work"):
                driver.close()
            task.cancel()
            result, = await asyncio.gather(task, return_exceptions=True)
            assert isinstance(result, asyncio.CancelledError)
        assert backend.watcher_count == 0


@pytest.mark.asyncio
async def test_sync_driver_preserves_body_and_close_failure():
    backend = MemoryBackend(object())
    body, cleanup = ValueError("transaction body"), OSError("input cleanup")

    class FailingClose(Input):
        def close(self):
            super().close()
            raise cleanup

    async with Execution(backend):
        with pytest.raises(ExceptionGroup) as caught:
            async with FailingClose(backend.clock):
                raise body
        assert caught.value.exceptions == (body, cleanup)
        async with Input(backend.clock) as replacement:
            await replacement.send(1)


@pytest.mark.asyncio
async def test_monitor_close_before_delivery_discards_captured_subscription_item():
    backend = MemoryBackend(object())
    captured = []
    monitor = SamplingMonitor(FallingEdge(backend.clock), capture=lambda event: captured.append(event.tick))

    async def close_at_first_falling_edge():
        await FallingEdge(backend.clock)
        await monitor.aclose()

    async with Execution(backend) as execution:
        async with execution.paused():
            closer = asyncio.create_task(close_at_first_falling_edge())
            await asyncio.sleep(0)
            monitor.start(execution)
        await closer
        assert captured == [1]
        with pytest.raises(MonitorClosedError):
            await monitor.recv()
        assert backend.watcher_count == 0


@pytest.mark.asyncio
async def test_protocol_candidate_cancel_failures_are_preserved_after_disarming(monkeypatch):
    backend = MemoryBackend(object())
    monitor = ReadyValidMonitor(SimpleNamespace(
        role=Role.MONITOR, clock=backend.clock, bits=Signal(), fire=FallingEdge(backend.clock),
    ))
    failures = []
    async with Execution(backend) as execution:
        monitor.start(execution)
        await FallingEdge(backend.clock)
        cancel = execution.reactor.cancel

        def interrupted_cancel(registration):
            active = registration.active
            cancel(registration)
            if active:
                failure = OSError("candidate cleanup")
                failures.append(failure)
                raise failure

        with monkeypatch.context() as patch:
            patch.setattr(execution.reactor, "cancel", interrupted_cancel)
            with pytest.raises(OSError) as caught:
                await monitor.aclose()
        assert failures == [caught.value]
        assert backend.watcher_count == 0
        await monitor.aclose()
        await ClockCycles(backend.clock, 1)


@pytest.mark.asyncio
async def test_self_cancelled_async_input_retires_and_later_input_still_runs():
    backend = MemoryBackend(object())

    class CancelledInput(AsyncInput):
        async def _drive_one(self, request):
            if request == 1:
                raise asyncio.CancelledError
            return await super()._drive_one(request)

    async with Execution(backend):
        async with CancelledInput(backend.clock) as driver:
            first, second = driver.send(1), driver.send(2)
            with pytest.raises(asyncio.CancelledError):
                await first
            assert (await second).tick == 2
            assert not driver.has_outstanding


@pytest.mark.asyncio
async def test_sync_driver_cannot_be_borrowed_by_another_execution():
    first, second = MemoryBackend(object()), MemoryBackend(object())
    async with Execution(first):
        async with Input(first.clock) as driver:
            async with Execution(second):
                with pytest.raises(RuntimeError, match="already bound to another Execution"):
                    await driver.send(1)
                assert second.tick == 0
            assert (await driver.send(2)).tick == 2
    assert first.watcher_count == second.watcher_count == 0
