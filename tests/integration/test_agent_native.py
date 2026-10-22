import asyncio

import pytest

from examples.transactions.pipeline import Input, Response
from examples.transactions.reference_model import (
    AccumulatorAgent, AccumulatorDut, AccumulatorModel, Command, Result,
)
from toffee import (
    Agent, AsyncSingleCycleDriver, ClockCycles, Execution, RisingEdge, SamplingMonitor,
    Scoreboard, ScoreboardMismatch, SyncSingleCycleDriver, PythonPredicateTrigger,
)


@pytest.fixture
def toy(make_toy):
    return make_toy()


class Model:
    def __init__(self):
        self.inputs = []

    def accept(self, request):
        self.inputs.append(request)
        return Response(request, request * 10)


def checked_agent(toy):
    return Agent("pipeline", driver=Input(toy), monitors={"output": toy.monitor},
                 response_monitor="output", clock=toy.clock, response_timeout_cycles=6)


@pytest.mark.asyncio
@pytest.mark.parametrize("driver_type", [SyncSingleCycleDriver, AsyncSingleCycleDriver])
async def test_agent_preserves_sync_and_async_driver_input_contract(toy, driver_type):
    toy.respond = False
    driver = driver_type(toy.clock, toy.first, idle=0)
    monitor = SamplingMonitor(RisingEdge(toy.clock), capture=lambda _: toy.first.U())
    agent = Agent("input", driver=driver, monitors={"accepted": monitor})
    async with asyncio.timeout(2), Execution(toy.backend, agents=[agent]) as execution:
        handle = agent.send(7)
        if driver_type is AsyncSingleCycleDriver:
            assert driver.has_outstanding  # Async submission is immediate.
        else:
            assert toy.first.U() == 0  # Sync send is still caller-driven.
        assert (await handle).tick == 2
        observation = await agent.recv("accepted")
        assert observation.value == 7
        assert observation.event.tick == 2
    assert not execution.reactor._drive_owners
    assert monitor._closed


@pytest.mark.asyncio
async def test_agent_multibatch_and_finish_keep_one_monitor_lifecycle(toy):
    calls = []
    start, close = toy.monitor.start, toy.monitor.aclose

    def record_start(execution):
        calls.append("start")
        return start(execution)

    async def record_close():
        calls.append("close")
        await close()

    toy.monitor.start, toy.monitor.aclose = record_start, record_close
    agent = checked_agent(toy).connect(Model())
    before = asyncio.all_tasks()
    async with asyncio.timeout(2), Execution(toy.backend, agents=[agent]) as execution:
        first = agent.submit(1)
        await agent.drain(timeout_cycles=10)
        assert await first == Response(1, 10)
        for request in (2, 3):
            agent.submit(request)
        await agent.finish(timeout_cycles=30, observe_cycles=1)
        assert agent.status.passed == 3
        assert calls == ["start"]
        assert not toy.monitor.closed
        with pytest.raises(RuntimeError, match="no longer accepts"):
            agent.submit(4)
        with pytest.raises(RuntimeError, match="exclusively"):
            await agent.recv("output")
    assert calls == ["start", "close"]
    assert not execution.reactor._monitor_consumers
    assert asyncio.all_tasks() == before


@pytest.mark.asyncio
async def test_two_scoreboards_cannot_consume_agent_monitor(toy):
    driver = Input(toy)
    agent = Agent("port", driver=driver, monitors={"output": toy.monitor},
                  response_monitor="output", clock=toy.clock, response_timeout_cycles=5)
    async with asyncio.timeout(2), Execution(toy.backend, agents=[agent]) as execution:
        second = Scoreboard("second").bind(
            execution, driver=driver, monitor=toy.monitor, clock=toy.clock,
            response_timeout_cycles=5, manage_monitor=False,
        )
        try:
            with pytest.raises(RuntimeError, match="already has"):
                await second.__aenter__()
        finally:
            await second.aclose()
        assert not toy.monitor.closed
        await agent.finish(timeout_cycles=5)


@pytest.mark.asyncio
async def test_passive_agent_reports_unobserved_monitor_failure(toy):
    failure = ValueError("passive sampling")

    def capture(event):
        raise failure

    monitor = SamplingMonitor(RisingEdge(toy.clock), capture=capture)
    agent = Agent("passive", monitors={"output": monitor})
    with pytest.raises(ValueError) as caught:
        async with asyncio.timeout(2), Execution(toy.backend, agents=[agent]):
            await ClockCycles(toy.clock, 3)
    assert caught.value is failure


@pytest.mark.asyncio
async def test_connected_send_preserves_acceptance_and_background_response_checking(toy):
    model = Model()
    agent = checked_agent(toy).connect(model)
    async with asyncio.timeout(2), Execution(toy.backend, agents=[agent]):
        first = agent.send(1)
        cancelled = agent.send(2)
        cancelled.cancel()
        assert model.inputs == []
        event = await first
        assert agent.status.completed == 0  # Acceptance precedes response.
        assert event.tick > 0
        third = agent.send(3)
        await third
        await agent.finish(timeout_cycles=30)
        assert model.inputs == [1, 3]
        assert agent.status.passed == 2
        assert agent.status.cancelled == 1


@pytest.mark.asyncio
async def test_cancelled_send_waiter_does_not_cancel_input(toy):
    model = Model()
    agent = checked_agent(toy).connect(model)
    async with asyncio.timeout(2), Execution(toy.backend, agents=[agent]):
        handle = agent.send(1)

        async def wait():
            return await handle

        async with agent._execution.paused():
            task = asyncio.create_task(wait())
            await asyncio.sleep(0)
            task.cancel()
            result, = await asyncio.gather(task, return_exceptions=True)
            assert isinstance(result, asyncio.CancelledError)
        await handle
        with pytest.raises(RuntimeError, match="accepted"):
            handle.cancel()
        await agent.finish(timeout_cycles=20)
        assert model.inputs == [1]


@pytest.mark.asyncio
async def test_unawaited_connected_send_failure_surfaces_at_execution_exit(toy):
    class BadModel:
        def accept(self, request):
            return Response(request, -1)

    agent = checked_agent(toy).connect(BadModel())
    with pytest.raises(ScoreboardMismatch):
        async with asyncio.timeout(2), Execution(toy.backend, agents=[agent]):
            agent.send(1)  # No orphan coroutine; checking still owns the response.
            await ClockCycles(toy.clock, 10)


@pytest.mark.asyncio
async def test_reference_failure_is_reported_once_and_ref_is_not_closed(toy):
    failure = ValueError("reference failure")

    class BadModel:
        def accept(self, request):
            raise failure

        def close(self):
            pytest.fail("Agent must not close a shared reference model")

    agent = checked_agent(toy).connect(BadModel())
    async with asyncio.timeout(2), Execution(toy.backend, agents=[agent]):
        transfer = agent.submit(1)
        with pytest.raises(ValueError) as caught:
            await transfer
        assert caught.value is failure


@pytest.mark.asyncio
async def test_explicit_expected_without_model(toy):
    agent = checked_agent(toy)
    async with asyncio.timeout(2), Execution(toy.backend, agents=[agent]):
        with pytest.raises(RuntimeError, match="explicit expected"):
            agent.submit(1)
        assert agent.status.submitted == 0
        transfer = agent.submit(1, expected=Response(1, 10))
        await agent.finish(timeout_cycles=20)
        assert await transfer == Response(1, 10)


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["memory", "native"])
async def test_expected_override_does_not_skip_stateful_model(backend):
    if backend == "native":
        pytest.importorskip("xspcomm")
    dut = AccumulatorDut(backend)
    model = AccumulatorModel()
    agent = AccumulatorAgent(dut, response_timeout_cycles=5).connect(model)
    try:
        async with asyncio.timeout(2), Execution(dut.backend, agents=[agent]):
            agent.submit(Command(1, 3), expected=Result(1, 3))
            second = agent.submit(Command(2, 5))
            await agent.finish(timeout_cycles=20)
            assert await second == Result(2, 8)
            assert model.total == 8
    finally:
        dut.close()


@pytest.mark.asyncio
async def test_connected_sync_send_stays_in_caller_and_rejects_submit(toy):
    model = Model()
    driver = SyncSingleCycleDriver(toy.clock, toy.first, idle=0)
    agent = Agent("sync", driver=driver, monitors={"out": toy.monitor},
                  response_monitor="out", clock=toy.clock, response_timeout_cycles=5).connect(model)
    async with asyncio.timeout(2), Execution(toy.backend, agents=[agent]):
        before = asyncio.all_tasks()
        operation = agent.send(1)
        assert agent.status.submitted == 0
        assert toy.first.U() == 0
        assert (await operation).tick == 2
        assert asyncio.all_tasks() == before  # No worker was introduced.
        with pytest.raises(RuntimeError, match="does not support submit"):
            agent.submit(2)
        await agent.send(2, expected=Response(2, 20))
        await agent.finish(timeout_cycles=20)
        assert model.inputs == [1, 2]
        assert agent.status.passed == 2


@pytest.mark.asyncio
async def test_connected_sync_send_cancellation_withdraws_unaccepted_input(toy):
    driver = SyncSingleCycleDriver(toy.clock, toy.first, idle=0)
    model = Model()
    agent = Agent("sync", driver=driver, monitors={"out": toy.monitor},
                  response_monitor="out", clock=toy.clock, response_timeout_cycles=5).connect(model)
    async with asyncio.timeout(2), Execution(toy.backend, agents=[agent]) as execution:
        async with execution.paused():
            task = asyncio.create_task(agent.send(1))
            await asyncio.sleep(0)
            task.cancel()
            result, = await asyncio.gather(task, return_exceptions=True)
            assert isinstance(result, asyncio.CancelledError)
        assert model.inputs == []
        assert toy.first.U() == 0
        await agent.send(2)
        await agent.finish(timeout_cycles=20)
        assert model.inputs == [2]
        assert agent.status.cancelled == 1


@pytest.mark.asyncio
async def test_two_agents_share_model_without_owning_it(toy):
    class SharedModel:
        def __init__(self):
            self.inputs = []

        def accept(self, request):
            self.inputs.append(request)
            return request

        def close(self):
            pytest.fail("model lifecycle belongs to the environment")

    model = SharedModel()
    agents = []
    for name, signal in (("a", toy.first), ("b", toy.second)):
        monitor = SamplingMonitor(
            PythonPredicateTrigger(name, lambda signal=signal: signal.U() != 0,
                                   sample=RisingEdge(toy.clock), mode="each_sample"),
            capture=lambda event, signal=signal: signal.U(),
        )
        agents.append(Agent(name, driver=SyncSingleCycleDriver(toy.clock, signal, idle=0),
                            monitors={"out": monitor}, response_monitor="out",
                            clock=toy.clock, response_timeout_cycles=2).connect(model))
    async with asyncio.timeout(2), Execution(toy.backend, agents=agents):
        # This test defines cross-interface order explicitly.
        await agents[0].send(1)
        await agents[0].drain(timeout_cycles=5)
        await agents[1].send(2)
        for agent in agents:
            await agent.finish(timeout_cycles=5)
    assert model.inputs == [1, 2]


@pytest.mark.asyncio
async def test_model_async_return_is_rejected_and_reported_once(toy):
    class BadModel:
        def accept(self, request):
            async def predict():
                return Response(request, 10)
            return predict()

    agent = checked_agent(toy).connect(BadModel())
    async with asyncio.timeout(2), Execution(toy.backend, agents=[agent]):
        transfer = agent.submit(1)
        with pytest.raises(TypeError, match="must be synchronous"):
            await transfer


def test_reference_connection_validates_before_start(toy):
    agent = checked_agent(toy)
    with pytest.raises(TypeError, match="synchronous accept"):
        agent.connect(object())

    class AsyncModel:
        async def accept(self, request):
            return request

    with pytest.raises(TypeError, match="synchronous accept"):
        agent.connect(AsyncModel())
    agent.connect(Model())
    with pytest.raises(RuntimeError, match="fresh, unconnected"):
        agent.connect(Model())


@pytest.mark.asyncio
async def test_binding_failure_releases_real_driver_and_monitor(toy):
    agent = Agent("bad-budget", driver=Input(toy), monitors={"out": toy.monitor},
                  response_monitor="out", clock=toy.clock, response_timeout_cycles=0)
    before = asyncio.all_tasks()
    with pytest.raises(ValueError):
        async with Execution(toy.backend, agents=[agent]):
            pytest.fail("invalid budget must fail startup")
    assert toy.monitor.closed
    assert asyncio.all_tasks() == before
