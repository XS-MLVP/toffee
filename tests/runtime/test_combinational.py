"""Explicit zero-time evaluation shares Execution's sampling and ownership."""
import asyncio
from types import SimpleNamespace

import pytest

from toffee import (
    AnyOf, Bin, ClockCycles, CombStable, CoverGroupDef, CoverPointDef,
    DriveStable, Execution, FallingEdge, MemoryBackend, PhaseEvent, RisingEdge,
    SamplingMonitor, SimulationNotSettledError, Value, ValueChange, WallTimeout,
    XCommClockBackend, XEventKind, XPhase, on, pytrigger, xtrigger,
)


@pytest.fixture(params=["clockless", "memory", "native"])
def circuit(request):
    evaluations = []
    if request.param == "native":
        x = pytest.importorskip("xspcomm")
        pins = SimpleNamespace(**{
            name: x.XData(8, x.XData.InOut) for name in ("a", "b", "out")
        })

        def evaluate(_):
            evaluations.append(1)
            pins.out.Set((pins.a.U() + pins.b.U()) & 255)
            return 0

        backend = XCommClockBackend(x.XClock(evaluate))
    else:
        pins = SimpleNamespace(**{
            name: SimpleNamespace(value=0) for name in ("a", "b", "out")
        })

        def evaluate():
            evaluations.append(1)
            pins.out.value = (pins.a.value + pins.b.value) & 255

        backend = MemoryBackend(None if request.param == "clockless" else object(),
                                on_comb=evaluate)
    yield pins, backend, evaluations
    backend.close()


def put(pin, value):
    if hasattr(pin, "Set"):
        pin.Set(value)
    else:
        pin.value = value


def get(pin):
    return pin.U() if hasattr(pin, "U") else pin.value


@pytest.mark.asyncio
async def test_repeated_evaluation_has_deltas_but_no_clock_edges(circuit):
    pins, backend, evaluations = circuit
    async with Execution(backend):
        events = []
        for a, b in ((1, 2), (250, 7), (0, 0)):
            put(pins.a, a)
            put(pins.b, b)
            event = await CombStable()
            assert get(pins.out) == (a + b) & 255
            assert isinstance(event, PhaseEvent)
            assert event.phase is XPhase.COMB_STABLE
            assert event.kind is XEventKind.COMB_STABLE
            events.append(event)
        assert [event.tick for event in events] == [0, 0, 0]
        assert [event.delta for event in events] == [1, 2, 3]
        assert len({event.event_id for event in events}) == 3
        assert evaluations
        if isinstance(backend, XCommClockBackend):
            assert backend.clock.GetHalfTick() == 0
    assert backend.watcher_count == 0


@pytest.mark.asyncio
async def test_same_request_batch_is_broadcast_once(circuit):
    _, backend, evaluations = circuit
    async with Execution(backend):
        first, second = await asyncio.gather(CombStable(), CombStable())
        assert first is second
        assert first.delta == backend.delta == 1
        assert evaluations


@xtrigger(sample=CombStable())
def five(pins):
    return pins.out == 5


@pytrigger(sample=CombStable())
def python_five(pins):
    return get(pins.out) == 5


@pytest.mark.asyncio
async def test_conditions_and_decorators_share_comb_sample(circuit):
    pins, backend, _ = circuit
    put(pins.a, 2)
    put(pins.b, 3)
    async with Execution(backend, default_sample=CombStable()):
        async def wait(trigger):
            return await trigger

        events = await asyncio.gather(*(wait(trigger) for trigger in (
            Value(pins.out, 5), five(pins), python_five(pins),
        )))
        assert all(event.tick == 0 and event.delta == 1 for event in events)
        assert events[0].value == 5


@pytest.mark.asyncio
async def test_pending_condition_samples_again_only_on_a_new_request(circuit):
    pins, backend, evaluations = circuit
    async with Execution(backend) as execution:
        pending = execution.reactor.register(Value(pins.out, 5, sample=CombStable()))
        await CombStable()
        assert not pending.future.done()
        samples = len(evaluations)
        for _ in range(5):
            await asyncio.sleep(0)
        assert len(evaluations) == samples and backend.tick == 0
        put(pins.a, 5)
        expected = await CombStable()
        actual = await pending.future
        assert actual.delta == expected.delta == 2 and actual.value == 5


@pytest.mark.asyncio
async def test_value_change_compares_registration_baseline(circuit):
    pins, backend, _ = circuit
    async with Execution(backend) as execution:
        pending = execution.reactor.register(ValueChange(pins.out, sample=CombStable()))
        put(pins.a, 7)
        await CombStable()
        event = await pending.future
        assert event.tick == 0 and event.value == 7


@pytest.mark.asyncio
async def test_monitor_is_passive_and_captures_before_waiters_resume(circuit):
    pins, backend, evaluations = circuit
    async with Execution(backend) as execution:
        async with SamplingMonitor(CombStable(), capture=lambda event: get(pins.out)).start(execution) as monitor:
            for _ in range(5):
                await asyncio.sleep(0)
            assert not evaluations
            put(pins.a, 9)
            event = await CombStable()
            put(pins.a, 10)
            item = await monitor.recv()
            assert item.value == 9 and item.event is event
            await CombStable()
            assert (await monitor.recv()).value == 10


@pytest.mark.asyncio
async def test_coverage_counts_separate_deltas_at_the_same_tick(circuit):
    pins, backend, evaluations = circuit
    coverage = CoverGroupDef("sum", (CoverPointDef("out", {
        "zero": Bin.values(0), "one": Bin.values(1),
    }),)).instantiate("dut").bind(trigger=CombStable(), fields={"out": pins.out})
    async with Execution(backend, coverage=[coverage]):
        for _ in range(3):
            await asyncio.sleep(0)
        assert not evaluations
        for value in (0, 1, 1):
            put(pins.a, value)
            await CombStable()
    report = coverage.report()
    assert coverage.samples == 3
    assert report["points"][0]["counts"] == {"zero": 1, "one": 2}
    if isinstance(backend, XCommClockBackend):
        assert report["sampling_backend"] == "native"


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy", ["native", "python"])
async def test_native_and_python_comb_coverage_agree(strategy):
    x = pytest.importorskip("xspcomm")
    signal = x.XData(8, x.XData.InOut)
    backend = XCommClockBackend(x.XClock(lambda _: 0))
    group = CoverGroupDef("comb", (CoverPointDef("value", {
        "one": Bin.values(1), "two": Bin.values(2),
        "transition": Bin.transition(1, 2),
    }),)).instantiate("dut").bind(trigger=CombStable(), fields={"value": signal}, strategy=strategy)
    try:
        async with Execution(backend, coverage=[group]):
            for value in (1, 2, 1, 2):
                signal.Set(value)
                await CombStable()
        assert group.samples == 4
        assert group.report()["points"][0]["counts"] == {"one": 2, "two": 2, "transition": 2}
        assert group.report()["sampling_backend"] == strategy
    finally:
        backend.close()


@pytest.mark.asyncio
async def test_native_comb_expressions_are_lowered_not_interpreted(monkeypatch):
    x = pytest.importorskip("xspcomm")
    signal = x.XData(128, x.XData.InOut)
    signal.Set(1 << 100)
    backend = XCommClockBackend(x.XClock(lambda _: 0))
    from toffee.ir import BinaryExpr, signal_expr
    from toffee.triggers import CompiledTrigger

    def forbidden(*args):
        pytest.fail("native CombStable must not interpret expression IR in Python")

    monkeypatch.setattr(BinaryExpr, "evaluate", forbidden)
    try:
        async with Execution(backend):
            event = await CompiledTrigger("wide", None, signal_expr(signal) == (1 << 100), CombStable())
            assert event.kind is XEventKind.CONDITION and event.value is True
            assert event.tick == 0 and event.delta == 1
    finally:
        backend.close()


@pytest.mark.asyncio
async def test_false_condition_can_time_out_without_advancing(circuit):
    pins, backend, _ = circuit
    async with Execution(backend):
        event = await AnyOf(Value(pins.out, 99, sample=CombStable()), WallTimeout(0.01))
        assert event.kind is XEventKind.TIMEOUT
        assert backend.tick == 0 and backend.delta == 1
    assert backend.watcher_count == 0


@pytest.mark.asyncio
async def test_zero_time_loop_has_a_budget_and_cleans_registrations(circuit):
    _, backend, _ = circuit
    with pytest.raises(SimulationNotSettledError, match="COMB_STABLE after 3 rounds"):
        async with Execution(backend, max_settle_rounds=3):
            for _ in range(4):
                await CombStable()
    assert backend.watcher_count == 0 and backend._owner is None
    async with Execution(backend):
        await CombStable()


@pytest.mark.asyncio
async def test_comb_cannot_skip_a_pending_drive_stable_barrier(circuit):
    pins, backend, _ = circuit
    if not backend.capabilities.half_step:
        pytest.skip("clockless backend")
    captures = []

    @on(DriveStable(backend.clock), capture=lambda event: captures.append((event.tick, get(pins.out))))
    async def observed(_):
        pass

    async with Execution(backend) as execution:
        subscription = execution.subscribe(observed.bind())
        await FallingEdge(backend.clock)
        put(pins.a, 6)
        first = await CombStable()
        stable = await DriveStable(backend.clock)
        assert first.tick == stable.tick == 1
        assert captures == [(1, 6)]
        rising = await RisingEdge(backend.clock)
        assert rising.tick == 2 and rising.delta == 0
        execution.reactor.cancel_subscription(subscription)


@pytest.mark.asyncio
async def test_clockless_backend_rejects_clock_triggers_but_stays_usable():
    backend = MemoryBackend()
    try:
        async with Execution(backend):
            for trigger in (RisingEdge(object()), ClockCycles(object(), 1)):
                with pytest.raises(RuntimeError, match="half-step"):
                    await trigger
            await CombStable()
    finally:
        backend.close()


@pytest.mark.asyncio
async def test_comb_requires_an_active_execution():
    with pytest.raises(RuntimeError, match="active Execution"):
        await CombStable()


@pytest.mark.asyncio
async def test_evaluation_error_releases_backend_and_allows_reuse():
    failure = ValueError("combinational evaluation failed")

    def broken():
        raise failure

    backend = MemoryBackend(on_comb=broken)
    try:
        with pytest.raises(ValueError) as caught:
            async with Execution(backend):
                await CombStable()
        assert caught.value is failure
        assert backend.watcher_count == 0 and backend._owner is None
        backend.on_comb = None
        async with Execution(backend):
            await CombStable()
    finally:
        backend.close()
