"""Invalid runtime definitions fail before starting simulation work."""

import asyncio
from types import SimpleNamespace

import pytest

from toffee import (AllOf, AnyOf, ClockCycles, Execution, FSM, Hold, MemoryBackend,
                      RisingEdge, Sequence, SimTimeout, State, TaskComplete, WallTimeout,
                      Within, on, pytrigger, xtrigger)
from toffee.backend import BackendCapabilities, RunLimit, StopReason
from toffee.ir import BinaryExpr, ConstantExpr, SignalExpr, UnaryExpr


@pytest.mark.parametrize("kwargs,message", [
    ({"max_batch_ticks": 0}, "max_batch_ticks"),
    ({"quantum_ms": 0}, "quantum_ms"),
    ({"budget_check_interval": 0}, "budget_check_interval"),
    ({"max_settle_rounds": True}, "integer"),
    ({"max_settle_rounds": 0}, "positive"),
    ({"default_sample": object()}, "default_sample"),
    ({"agents": [object()]}, "Agent"),
    ({"coverage": [object()]}, "CoverGroup"),
])
def test_execution_rejects_invalid_configuration(kwargs, message):
    with pytest.raises((TypeError, ValueError), match=message):
        Execution(MemoryBackend(object()), **kwargs)


def test_execution_requires_explicit_stable_backend_capabilities():
    with pytest.raises(TypeError, match="capabilities"):
        Execution(object())
    for backend in (SimpleNamespace(capabilities=BackendCapabilities(half_step=False, stable_sample=True)),
                    SimpleNamespace(capabilities=BackendCapabilities(half_step=True, stable_sample=False))):
        with pytest.raises(RuntimeError, match="half-step"):
            Execution(backend)


@pytest.mark.parametrize("factory,message", [
    (lambda: RunLimit(max_ticks=0), "max_ticks"),
    (lambda: RunLimit(max_wall_time_ms=0), "max_wall_time"),
    (lambda: RunLimit(budget_check_interval=0), "budget_check"),
    (lambda: ClockCycles(object(), -1), "positive"),
    (lambda: SimTimeout(-1, clock=object()), "positive"),
    (lambda: WallTimeout(-1), "non-negative"),
    (lambda: AnyOf(), "at least one"),
    (lambda: AllOf(), "at least one"),
    (lambda: Within(3, 2, True), "minimum"),
    (lambda: Hold(True, cycles=0), "positive"),
    (lambda: Sequence(), "at least one"),
    (lambda: FSM(start="idle", states={}), "at least one"),
    (lambda: FSM(start="missing", states={"idle": State()}), "start state"),
    (lambda: FSM(start="idle", states={"idle": State().when(True).goto("missing")}), "unknown"),
    (lambda: State().otherwise().trigger().when(True).trigger(), "last"),
    (lambda: xtrigger(sample=object()), "sample"),
    (lambda: pytrigger(sample=object()), "sample"),
    (lambda: xtrigger(mode="invalid"), "mode"),
    (lambda: on(RisingEdge(object()), delivery="invalid"), "delivery"),
    (lambda: on(RisingEdge(object()), capacity=0), "capacity"),
    (lambda: on(RisingEdge(object()), capture=1), "callable"),
    (lambda: on(RisingEdge(object()))(lambda _: None), "async def"),
])
def test_trigger_and_subscription_configuration_has_clear_errors(factory, message):
    with pytest.raises((TypeError, ValueError), match=message):
        factory()


def test_expression_operators_evaluate_nested_signals_and_reject_unknown_operations():
    source = SignalExpr(("payload",)).value
    dut = SimpleNamespace(payload=SimpleNamespace(value=3))
    assert (source < 4).evaluate(dut) and (source > 2).evaluate(dut)
    assert (True & (source == 3)).evaluate(dut)
    assert (False | (source != 2)).evaluate(dut)
    assert ((source == 3) | (source == 4)).evaluate(dut)
    for expression in (UnaryExpr("invalid", source), BinaryExpr("invalid", source, ConstantExpr(3))):
        with pytest.raises(ValueError, match="unsupported"):
            expression.evaluate(dut)
    assert str(StopReason.RUN_LIMIT) == StopReason.RUN_LIMIT.value


@pytest.mark.asyncio
async def test_allof_failure_cancels_siblings_but_preserves_source_tasks():
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def pending():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    error = ValueError("external source failed")

    async def failing():
        await started.wait()
        raise error

    with pytest.raises(ValueError) as caught:
        await AllOf(pending(), failing())
    assert caught.value is error and stopped.is_set()
    future = asyncio.get_running_loop().create_future()
    adapted = asyncio.ensure_future(TaskComplete(future))
    future.set_result(17)
    result = await adapted
    assert result.value == 17 and result.source is future


@pytest.mark.asyncio
async def test_runtime_definition_failures_leave_execution_usable():
    backend = MemoryBackend(object())

    async def handler(event):
        pass

    spec = on(lambda: object())(handler)
    with pytest.raises(TypeError, match="XTrigger"):
        spec.bind()
    async with Execution(backend) as execution:
        with pytest.raises(RuntimeError, match="twice"):
            await execution.__aenter__()
        event = await ClockCycles(backend.clock, 1)
        assert event.tick == 2
    assert backend.watcher_count == 0


@pytest.mark.asyncio
async def test_pytrigger_rejects_awaitable_predicates_without_running_them():
    class AsyncValue:
        def __await__(self):
            raise AssertionError("predicate was incorrectly awaited")
            yield

    backend = MemoryBackend(object())

    @pytrigger(sample=RisingEdge("clock"))
    def predicate(dut):
        return AsyncValue()

    with pytest.raises(TypeError, match="synchronous"):
        async with Execution(backend):
            await predicate(SimpleNamespace(clock=backend.clock))
    assert backend.watcher_count == 0 and backend._owner is None
