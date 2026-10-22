import asyncio

import pytest

from toffee import Agent, Execution, MemoryBackend


class Monitor:
    def __init__(self, name, trace, *, start_error=None, close_error=None):
        self.name, self.trace = name, trace
        self.start_error, self.close_error = start_error, close_error

    def start(self, execution):
        self.trace.append(f"{self.name}.start")
        if self.start_error is not None:
            raise self.start_error
        return self

    async def aclose(self):
        self.trace.append(f"{self.name}.close")
        if self.close_error is not None:
            raise self.close_error


class Driver:
    def __init__(self, trace, *, enter_error=None, exit_error=None):
        self.trace = trace
        self.enter_error, self.exit_error = enter_error, exit_error

    async def __aenter__(self):
        self.trace.append("driver.enter")
        if self.enter_error is not None:
            raise self.enter_error
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        self.trace.append("driver.exit")
        if self.exit_error is not None:
            if exc is not None:
                raise BaseExceptionGroup("driver", [exc, self.exit_error])
            raise self.exit_error


@pytest.mark.asyncio
async def test_agent_starts_observation_first_and_closes_in_reverse():
    trace = []
    driver = Driver(trace)
    monitors = {name: Monitor(name, trace) for name in ("request", "response")}
    agent = Agent("port", driver=driver, monitors=monitors)
    monitors.clear()
    execution = Execution(MemoryBackend(object()), agents=[agent])
    async with execution:
        assert agent.driver is driver
        assert list(agent.monitors) == ["request", "response"]
        assert trace == ["request.start", "response.start", "driver.enter"]
    assert execution.backend._owner is None
    with pytest.raises(RuntimeError, match="closed"):
        async with Execution(MemoryBackend(object()), agents=[agent]):
            pytest.fail("closed Agent cannot restart")
    assert trace == [
        "request.start", "response.start", "driver.enter",
        "driver.exit", "response.close", "request.close",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("where", ["monitor", "driver"])
async def test_agent_start_failure_cleans_started_components_and_previous_agents(where):
    trace = []
    failure = ValueError("start failure")
    first = Agent("first", monitors={"first": Monitor("first", trace)})
    partial = Agent("partial", driver=Driver(trace, enter_error=failure if where == "driver" else None),
                    monitors={
                        "one": Monitor("one", trace),
                        "two": Monitor("two", trace, start_error=failure if where == "monitor" else None),
                    })
    never = Agent("never", monitors={"never": Monitor("never", trace)})
    backend = MemoryBackend(object())
    before = asyncio.all_tasks()
    with pytest.raises(ValueError) as caught:
        async with Execution(backend, agents=[first, partial, never]):
            pytest.fail("failed startup must not enter the body")
    assert caught.value is failure
    assert backend._owner is None
    assert backend.watcher_count == 0
    assert asyncio.all_tasks() == before
    expected = ["first.start", "one.start", "two.start"]
    if where == "driver":
        expected.append("driver.enter")
    assert trace == expected + ["two.close", "one.close", "first.close"]


@pytest.mark.asyncio
@pytest.mark.parametrize("grouped", [False, True])
async def test_agent_preserves_body_and_every_independent_cleanup_failure(grouped):
    trace = []
    body = ExceptionGroup("body", [ValueError("first"), TypeError("second")]) if grouped else ValueError("body")
    drive_error, monitor_error = RuntimeError("driver"), OSError("monitor")
    agent = Agent("errors", driver=Driver(trace, exit_error=drive_error), monitors={
        "one": Monitor("one", trace, close_error=drive_error),  # Same diagnostic, reported once.
        "two": Monitor("two", trace, close_error=monitor_error),
    })
    backend = MemoryBackend(object())
    with pytest.raises(ExceptionGroup) as caught:
        async with Execution(backend, agents=[agent]):
            raise body
    assert caught.value.exceptions == (body, drive_error, monitor_error)
    assert trace[-3:] == ["driver.exit", "two.close", "one.close"]
    assert backend._owner is None


@pytest.mark.asyncio
async def test_agent_external_cancel_does_not_cancel_host_task():
    trace = []
    host = asyncio.create_task(asyncio.Event().wait())
    ready = asyncio.Event()
    backend = MemoryBackend(object())
    agent = Agent("passive", monitors={"output": Monitor("output", trace)})

    async def owner():
        async with Execution(backend, agents=[agent]):
            ready.set()
            await asyncio.Event().wait()

    try:
        async with asyncio.timeout(2):
            task = asyncio.create_task(owner())
            await ready.wait()
            task.cancel()
            result, = await asyncio.gather(task, return_exceptions=True)
            assert isinstance(result, asyncio.CancelledError)
            assert trace == ["output.start", "output.close"]
            assert not host.done()
            assert backend._owner is None
    finally:
        host.cancel()
        await asyncio.gather(host, return_exceptions=True)


def test_agent_validates_composition_and_execution_rejects_shared_components():
    monitor = Monitor("one", [])
    with pytest.raises(ValueError, match="requires"):
        Agent("empty")
    with pytest.raises(ValueError, match="single owning name"):
        Agent("duplicate", monitors={"one": monitor, "two": monitor})
    with pytest.raises(ValueError, match="names"):
        Agent("names", monitors={"": monitor})
    with pytest.raises(TypeError, match="context management"):
        Agent("driver", driver=object())
    a = Agent("a", monitors={"one": monitor})
    b = Agent("b", monitors={"two": monitor})
    for agents in ([a, a], [a, b]):
        with pytest.raises(ValueError, match="distinct"):
            Execution(MemoryBackend(object()), agents=agents)
    with pytest.raises(TypeError, match="Agent instances"):
        Execution(MemoryBackend(object()), agents=[monitor])


@pytest.mark.asyncio
async def test_agent_requires_own_execution_and_has_no_context_api():
    agent = Agent("passive", monitors={"output": Monitor("output", [])})
    assert not hasattr(agent, "__aenter__")
    assert not hasattr(agent, "bind")
    with pytest.raises(RuntimeError, match="active Execution"):
        agent.send(1)
    async with Execution(MemoryBackend(object()), agents=[agent]):
        with pytest.raises(RuntimeError, match="passive"):
            agent.send(1)
        with pytest.raises(RuntimeError, match="no response checker"):
            agent.submit(1)
        with pytest.raises(RuntimeError, match="already started"):
            async with Execution(MemoryBackend(object()), agents=[agent]):
                pytest.fail("Agent cannot be borrowed by another execution")
        # Failed nested startup must not close the original instance.
        assert agent._active


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_start", [False, True])
async def test_agent_owns_partial_async_startup(cancel_start):
    trace = []
    entered, proceed = asyncio.Event(), asyncio.Event()

    class StartingDriver(Driver):
        async def __aenter__(self):
            entered.set()
            try:
                await proceed.wait()
            except asyncio.CancelledError:
                self.trace.append("driver.enter.rollback")
                raise
            return await super().__aenter__()

    backend = MemoryBackend(object())
    agent = Agent("starting", driver=StartingDriver(trace),
                  monitors={"output": Monitor("output", trace)})

    async def owner():
        async with Execution(backend, agents=[agent]):
            trace.append("body")

    async with asyncio.timeout(2):
        task = asyncio.create_task(owner())
        await entered.wait()
        with pytest.raises(RuntimeError, match="active Execution"):
            agent.send(1)
        if cancel_start:
            task.cancel()
        else:
            proceed.set()
        result, = await asyncio.gather(task, return_exceptions=True)
        if cancel_start:
            assert isinstance(result, asyncio.CancelledError)
            assert trace == ["output.start", "driver.enter.rollback", "output.close"]
        else:
            assert result is None
            assert trace == ["output.start", "driver.enter", "body", "driver.exit", "output.close"]
        assert backend._owner is None
