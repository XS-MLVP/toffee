"""Failures at backend and subscription boundaries retain diagnostics and release work."""

import asyncio

import pytest

from toffee import (
    ClockCycles, Execution, FallingEdge, MemoryBackend, RisingEdge, RunResult,
    StopReason, XPhase, on,
)


@pytest.mark.asyncio
async def test_inactive_execution_rejects_operations_without_scheduling_work():
    backend = MemoryBackend(object())
    execution = Execution(backend)

    async def handler(event):
        pass

    spec = on(RisingEdge(backend.clock))(handler).bind()
    with pytest.raises(RuntimeError, match="active Execution"):
        execution.subscribe(spec)
    coroutine = handler(None)
    try:
        with pytest.raises(RuntimeError, match="current active Execution"):
            execution.external_task(coroutine)
    finally:
        coroutine.close()
    async with execution:
        assert (await ClockCycles(backend.clock, 1)).tick == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_source", ["awaitable-capture", "rearm", "stalled"])
async def test_runtime_failures_release_all_watchers(failure_source, monkeypatch):
    backend = MemoryBackend(object())
    handled = []

    async def unexpected_capture():
        pytest.fail("an awaitable capture must never run")

    def capture(event):
        return unexpected_capture() if failure_source == "awaitable-capture" else event

    @on(RisingEdge(backend.clock), capture=capture)
    async def handler(event):
        handled.append(event)

    if failure_source == "rearm":
        monkeypatch.setattr(backend, "rearm", lambda handle: False)
    elif failure_source == "stalled":
        monkeypatch.setattr(backend, "run_until", lambda limit: RunResult(0, XPhase.FALLING_STABLE, StopReason.BACKEND_STOP))
    error = TypeError if failure_source == "awaitable-capture" else RuntimeError
    message = {"awaitable-capture": "capture must be synchronous", "rearm": "failed to rearm", "stalled": "stalled"}[failure_source]
    with pytest.raises(error, match=message):
        async with Execution(backend) as execution:
            execution.subscribe(handler.bind())
            await ClockCycles(backend.clock, 2)
    assert handled == [] and backend.watcher_count == 0 and backend._owner is None


@pytest.mark.asyncio
async def test_reactor_close_cancels_pending_registrations_and_rejects_new_work():
    backend = MemoryBackend(object())
    async with Execution(backend) as execution:
        async with execution.paused():
            reactor = execution.reactor
            pending = reactor.register(RisingEdge(backend.clock))
            reactor.close()
            assert pending.future.cancelled() and backend.watcher_count == 0
            reactor.close()
            with pytest.raises(RuntimeError, match="closed"):
                reactor.register(FallingEdge(backend.clock))

            @on(FallingEdge(backend.clock))
            async def handler(event):
                pass

            with pytest.raises(RuntimeError, match="closed"):
                reactor.subscribe(handler.bind())
    assert backend._owner is None


@pytest.mark.asyncio
async def test_execution_preserves_independent_cleanup_failures(monkeypatch):
    backend = MemoryBackend(object())
    execution = Execution(backend)
    body, reactor_failure, reset_failure = ValueError("body"), OSError("reactor cleanup"), RuntimeError("backend reset")
    with pytest.raises(ExceptionGroup) as caught:
        async with execution:
            reactor_close = execution.reactor.aclose
            clear = backend.clear_execution_state

            async def broken_close():
                await reactor_close()
                raise reactor_failure

            def broken_clear():
                clear()
                raise reset_failure

            monkeypatch.setattr(execution.reactor, "aclose", broken_close)
            monkeypatch.setattr(backend, "clear_execution_state", broken_clear)
            raise body
    assert caught.value.exceptions == (body, reactor_failure, reset_failure)
    assert backend._owner is None and backend.watcher_count == 0


def test_event_loop_with_read_only_callback_hook_is_rejected_cleanly():
    class ReadOnlyLoop(asyncio.SelectorEventLoop):
        def __setattr__(self, name, value):
            if name == "call_soon":
                raise AttributeError("callback hook is read-only")
            super().__setattr__(name, value)

    backend = MemoryBackend(object())

    async def scenario():
        with pytest.raises(RuntimeError, match="callback observation"):
            async with Execution(backend):
                pytest.fail("unsupported callback hook must fail before the body")

    with asyncio.Runner(loop_factory=ReadOnlyLoop) as runner:
        runner.run(scenario())
    assert backend._owner is None and backend.watcher_count == 0


@pytest.mark.asyncio
async def test_cancelled_clock_demand_does_not_advance_the_backend():
    backend = MemoryBackend(object())
    async with Execution(backend) as execution:
        async with execution.paused():
            waiting = asyncio.ensure_future(ClockCycles(backend.clock, 1))
            await asyncio.sleep(0)
            waiting.cancel()
            await asyncio.gather(waiting, return_exceptions=True)
        await asyncio.sleep(0)
        assert backend.tick == 0 and backend.watcher_count == 0
        assert (await ClockCycles(backend.clock, 1)).tick == 2


@pytest.mark.asyncio
async def test_execution_rejects_non_base_event_loop_without_claiming_backend(monkeypatch):
    backend = MemoryBackend(object())
    with monkeypatch.context() as patch:
        patch.setattr(asyncio, "get_running_loop", lambda: asyncio.AbstractEventLoop())
        with pytest.raises(RuntimeError, match="unsupported loop"):
            async with Execution(backend):
                pytest.fail("unsupported event loop must fail before entering the body")
    assert backend._owner is None and backend.watcher_count == 0
    async with Execution(backend):
        await ClockCycles(backend.clock, 1)
