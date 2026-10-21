"""Backend contracts exercised through registrations and real native expressions."""

from types import SimpleNamespace

import pytest

from xreactor import (
    Bundle, ClockCycles, DriveStable, Execution, FallingEdge, MemoryBackend, RisingEdge,
    RunLimit, SimTimeout, Value, ValueChange, XCommClockBackend, XEventKind, pytrigger, xtrigger, FSM, State,
)
from xreactor.ir import BinaryExpr, BoundSignalExpr, ConstantExpr, SignalExpr, UnaryExpr, XExpr, signal_expr
from xreactor.triggers import CompiledTrigger, XTrigger


@pytest.fixture(params=["memory", "native"])
def backend(request):
    if request.param == "memory":
        result = MemoryBackend(object())
    else:
        xspcomm = pytest.importorskip("xspcomm")
        result = XCommClockBackend(xspcomm.XClock(lambda _: 0))
    yield result
    result.close()


def test_stale_handles_and_closed_backend(backend):
    old = backend.arm(RisingEdge(backend.clock))
    assert backend.disarm(old)
    current = backend.arm(FallingEdge(backend.clock))
    assert not backend.disarm(old) and not backend.rearm(old)
    assert backend.watcher_count == 1
    assert backend.disarm(current)
    backend.close()
    backend.close()
    for operation in (lambda: backend.arm(RisingEdge(backend.clock)),
                      lambda: backend.run_until(RunLimit())):
        with pytest.raises(RuntimeError, match="closed"):
            operation()


@pytest.mark.parametrize("bound", [False, True])
def test_bundle_expression_builder_reuses_symbolic_and_live_leaves(backend, bound):
    if isinstance(backend, MemoryBackend):
        signal = SimpleNamespace(value=0, width=1)

        def set_value(value):
            signal.value = value
    else:
        import xspcomm
        signal = xspcomm.XData(1, xspcomm.XData.InOut)
        set_value = signal.Set
    ports = Bundle(flag=signal)
    assert ports.flag is signal
    if bound:
        expression = signal_expr(ports.flag)
        assert signal_expr(expression) is expression
        trigger = CompiledTrigger("bound", None, expression, RisingEdge(backend.clock))
    else:
        @xtrigger(sample=RisingEdge(backend.clock))
        def condition(bundle):
            return signal_expr(bundle.flag)
        trigger = condition(ports)
        assert isinstance(trigger.program, SignalExpr)
    handle = backend.arm(trigger)
    assert not backend.run_until(RunLimit(max_ticks=2)).hits
    set_value(1)
    assert backend.run_until(RunLimit(max_ticks=2)).hits[0].kind is XEventKind.CONDITION
    assert backend.disarm(handle)


def test_countdown_and_value_change_rearm_in_memory():
    backend = MemoryBackend("clock")
    countdown = backend.arm(ClockCycles("clock", 2))
    assert not backend.run_until(RunLimit(max_ticks=2)).hits
    assert backend.rearm(countdown)
    assert not backend.run_until(RunLimit(max_ticks=2)).hits
    assert backend.run_until(RunLimit(max_ticks=2)).hits[0].value == 2
    backend.disarm(countdown)
    signal = SimpleNamespace(value=1)
    change = backend.arm(ValueChange(signal, sample=RisingEdge("clock")))
    assert not backend.run_until(RunLimit(max_ticks=2)).hits
    signal.value = 2
    assert backend.rearm(change)
    assert not backend.run_until(RunLimit(max_ticks=2)).hits
    signal.value = 3
    assert backend.run_until(RunLimit(max_ticks=2)).hits[0].value == 3
    backend.close()


def test_drive_sample_without_demand_and_invalid_phase(backend):
    assert backend.sample_drive_stable() is None
    handle = backend.arm(DriveStable(backend.clock))
    if isinstance(backend, MemoryBackend):
        with pytest.raises(RuntimeError, match="preceding FallingStable"):
            backend.sample_drive_stable()
    backend.disarm(handle)


def test_native_backend_constructor_contract():
    with pytest.raises(TypeError, match="half-step API"):
        XCommClockBackend(object())
    xspcomm = pytest.importorskip("xspcomm")
    clock = xspcomm.XClock(lambda _: 0)
    with pytest.raises(TypeError, match="on_phase"):
        XCommClockBackend(clock, on_phase=lambda phase, tick: None)
    with pytest.raises(ValueError, match="capacity"):
        XCommClockBackend(clock, capacity=0)


@pytest.mark.parametrize("missing_import", [False, True])
def test_native_backend_reports_incompatible_module(monkeypatch, missing_import):
    xspcomm = pytest.importorskip("xspcomm")
    clock = xspcomm.XClock(lambda _: 0)

    def incompatible(name):
        if missing_import:
            raise ImportError("module unavailable")
        return SimpleNamespace()

    monkeypatch.setattr("xreactor.backend.importlib.import_module", incompatible)
    with pytest.raises(RuntimeError, match="requires xspcomm" if missing_import else "without.*API"):
        XCommClockBackend(clock)


@pytest.fixture
def native():
    xspcomm = pytest.importorskip("xspcomm")
    backend = XCommClockBackend(xspcomm.XClock(lambda _: 0))
    yield backend, xspcomm
    backend.close()


@pytest.mark.parametrize("expression,message", [
    (ConstantExpr("one"), "bool or int"),
    (ConstantExpr(-1), "negative constants"),
    (ConstantExpr(1 << 64), "fit uint64"),
    (UnaryExpr("negate", ConstantExpr(1)), "unary op"),
    (BinaryExpr("unsupported_add", ConstantExpr(1), ConstantExpr(2)), "binary op"),
    (XExpr(), "IR node"),
])
def test_native_expression_errors_do_not_allocate_watchers(native, expression, message):
    backend, _ = native
    trigger = CompiledTrigger("invalid", None, expression, RisingEdge(backend.clock))
    with pytest.raises((TypeError, ValueError), match=message):
        backend.arm(trigger)
    assert backend.watcher_count == 0
    valid = backend.arm(RisingEdge(backend.clock))
    assert backend.run_until(RunLimit(max_ticks=2)).hits[0].kind is XEventKind.CLOCK_RISE
    backend.disarm(valid)


@pytest.mark.parametrize("problem,message", [
    ("width", "equal widths"),
    ("negative", "does not fit unsigned"),
    ("overflow", "does not fit unsigned"),
    ("combined", "direct signal"),
    ("signal-alone", "directly in a comparison"),
    ("bound-alone", "directly in a comparison"),
])
def test_native_wide_expression_constraints(native, problem, message):
    backend, xspcomm = native
    wide = xspcomm.XData(128, xspcomm.XData.InOut)
    other = xspcomm.XData(129, xspcomm.XData.InOut)
    dut = SimpleNamespace(wide=wide, other=other)
    expr = SignalExpr(("wide",))
    if problem == "width":
        expr = expr == SignalExpr(("other",))
    elif problem == "negative":
        expr = expr == -1
    elif problem == "overflow":
        expr = expr == (1 << 128)
    elif problem == "combined":
        expr = expr == (SignalExpr(("other",)) & True)
    elif problem == "bound-alone":
        expr = BoundSignalExpr(wide)
    with pytest.raises((ValueError, TypeError), match=message):
        backend.arm(CompiledTrigger("wide", dut, expr, RisingEdge(backend.clock)))
    assert backend.watcher_count == 0


@pytest.mark.asyncio
async def test_reverse_wide_comparison_and_native_timeout(native):
    backend, xspcomm = native
    wide = xspcomm.XData(128, xspcomm.XData.InOut)
    value = (1 << 100) + 3
    wide.Set(value)
    expr = ConstantExpr(value) <= BoundSignalExpr(wide)
    async with Execution(backend):
        event = await CompiledTrigger("reverse", None, expr, RisingEdge(backend.clock))
        assert event.kind is XEventKind.CONDITION
        timeout = await SimTimeout(cycles=2, clock=backend.clock)
        assert timeout.kind is XEventKind.TIMEOUT and timeout.value == 2


def test_native_registration_contract_and_owner_conflicts(native):
    backend, xspcomm = native
    owner = object()
    backend.acquire(owner)
    with pytest.raises(RuntimeError, match="active Execution"):
        backend.acquire(object())
    backend.release(owner)
    with pytest.raises(ValueError, match="neither"):
        backend.arm(RisingEdge(object()))
    with pytest.raises(TypeError, match="XData"):
        backend.arm(Value(SimpleNamespace(value=1), 1, sample=RisingEdge(backend.clock)))
    signal = xspcomm.XData(1, xspcomm.XData.InOut)
    with pytest.raises(TypeError, match="integer expected"):
        backend.arm(Value(signal, "one", sample=RisingEdge(backend.clock)))
    handle = backend.arm(RisingEdge(backend.clock))
    with pytest.raises(RuntimeError, match="active watchers"):
        backend.clear_execution_state()
    backend.disarm(handle)


@pytest.mark.parametrize("problem,message", [
    ("phase", "unknown xcomm phase"),
    ("reason", "unknown xcomm stop reason"),
    ("kind", "unknown xcomm hit kind"),
])
def test_native_result_rejects_unknown_abi_values(native, monkeypatch, problem, message):
    backend, _ = native
    handle = backend.arm(RisingEdge(backend.clock))
    hit = SimpleNamespace(slot=handle.slot, generation=handle.generation, kind=99)
    result = SimpleNamespace(stopped_phase=99 if problem == "phase" else 1,
                             stop_reason=99 if problem == "reason" else 0,
                             hits=[hit] if problem == "kind" else [],
                             advanced_ticks=1, IsPhaseBarrier=lambda: True)
    monkeypatch.setattr(backend._engine, "RunUntil", lambda *args: result)
    with pytest.raises(RuntimeError, match=message):
        backend.run_until(RunLimit())
    assert backend.disarm(handle) and backend.watcher_count == 0


@pytest.mark.asyncio
async def test_clock_callback_error_propagates_with_cleanup(native):
    backend, _ = native
    failure = ValueError("clock callback failed")

    def broken(cycle, *args):
        raise failure

    backend.clock.StepRis(broken)
    with pytest.raises(ValueError) as caught:
        async with Execution(backend):
            await RisingEdge(backend.clock)
    assert caught.value is failure
    assert backend.watcher_count == 0 and backend._owner is None


@pytest.mark.parametrize("missing_api", [False, True])
def test_drive_stable_native_boundary_failures(native, monkeypatch, missing_api):
    backend, _ = native
    handle = backend.arm(DriveStable(backend.clock))

    class EngineProxy:
        def __getattr__(self, name):
            if name == "SamplePhase":
                raise AttributeError(name)
            return getattr(engine, name)

    engine = backend._engine
    if missing_api:
        monkeypatch.setattr(backend, "_engine", EngineProxy())
    else:
        def broken(*args):
            raise RuntimeError("sample phase failed")
        monkeypatch.setattr(engine, "SamplePhase", broken)
    with pytest.raises(RuntimeError, match="SamplePhase support" if missing_api else "sample phase failed"):
        backend.sample_drive_stable()
    assert backend.disarm(handle) and backend.watcher_count == 0


@pytest.mark.asyncio
async def test_backend_reports_unsupported_trigger_and_stays_reusable(backend):
    with pytest.raises(TypeError, match="support|lower"):
        async with Execution(backend):
            await XTrigger()
    assert backend.watcher_count == 0 and backend._owner is None
    async with Execution(backend):
        await ClockCycles(backend.clock, 1)


@pytest.mark.asyncio
async def test_native_falling_value_sample(native):
    backend, xspcomm = native
    signal = xspcomm.XData(8, xspcomm.XData.InOut)
    signal.Set(3)
    async with Execution(backend):
        result = await Value(signal, 3, sample=FallingEdge(backend.clock))
        assert result.tick == 1 and result.value == 3


@pytest.mark.parametrize("bound,exception", [(False, False), (True, False), (False, True)])
def test_invalid_native_signal_lowering_reports_cause(native, monkeypatch, bound, exception):
    backend, xspcomm = native
    signal = xspcomm.XData(8, xspcomm.XData.InOut)
    dut = SimpleNamespace(data=signal)
    expression = BoundSignalExpr(signal) if bound else SignalExpr(("data",))
    failure = TypeError("native signal rejected")

    def invalid(*args):
        if exception:
            raise failure
        return -1

    monkeypatch.setattr(backend._engine, "ExprNewSignal", invalid)
    with pytest.raises(TypeError if exception else ValueError, match="XData" if exception else "resolved to null") as caught:
        backend.arm(CompiledTrigger("invalid-signal", dut, expression, RisingEdge(backend.clock)))
    if exception:
        assert caught.value.__cause__ is failure
    assert backend.watcher_count == 0


@pytest.mark.asyncio
async def test_native_python_sample_rearm_failure_is_reported(native, monkeypatch):
    backend, _ = native

    @pytrigger(sample=RisingEdge("clock"))
    def never(dut):
        return False

    monkeypatch.setattr(backend._engine, "RearmSample", lambda handle: False)
    with pytest.raises(RuntimeError, match="rearm Python sampling watcher"):
        async with Execution(backend):
            await never(SimpleNamespace(clock=backend.clock))
    assert backend.watcher_count == 0 and backend._owner is None


@pytest.mark.asyncio
async def test_rejected_async_predicate_closes_its_unstarted_coroutine(backend):
    import inspect

    created = []

    async def work():
        pytest.fail("an asynchronous predicate must never run")

    @pytrigger(sample=RisingEdge("clock"))
    def predicate(dut):
        coroutine = work()
        created.append(coroutine)
        return coroutine

    try:
        with pytest.raises(TypeError, match="synchronous"):
            async with Execution(backend):
                await predicate(SimpleNamespace(clock=backend.clock))
        assert len(created) == 1 and inspect.getcoroutinestate(created[0]) == inspect.CORO_CLOSED
        assert backend.watcher_count == 0 and backend._owner is None
    finally:
        for coroutine in created:
            coroutine.close()


def test_memory_named_clocks_match_by_value():
    name, same_name = "clock", "".join(("cl", "ock"))
    assert name == same_name and name is not same_name
    backend = MemoryBackend(name)
    handle = backend.arm(RisingEdge(same_name))
    assert backend.run_until(RunLimit(max_ticks=2)).hits[0].source == name
    backend.disarm(handle)
    backend.close()


def test_native_rejects_invalid_sampling_phase_and_signal_width(native, monkeypatch):
    backend, xspcomm = native
    signal = xspcomm.XData(8, xspcomm.XData.InOut)
    with pytest.raises(TypeError, match="sample must be"):
        backend.arm(Value(signal, 1, sample=ClockCycles(backend.clock, 1)))

    def broken_width():
        raise TypeError("width API failed")

    monkeypatch.setattr(signal, "W", broken_width)
    with pytest.raises(TypeError, match="XData") as caught:
        backend.arm(Value(signal, 1, sample=RisingEdge(backend.clock)))
    assert str(caught.value.__cause__) == "width API failed" and backend.watcher_count == 0


def test_native_rejects_unknown_fsm_terminal_id(native, monkeypatch):
    backend, _ = native
    program = FSM(start="wait", states={"wait": State().when(True).trigger("DONE")})
    trigger = CompiledTrigger("fsm", None, program, RisingEdge(backend.clock))
    handle = backend.arm(trigger)
    hit = SimpleNamespace(slot=handle.slot, generation=handle.generation, kind=5, value=99,
                          event_id=1, tick=2, phase=1, x_mask=0, source_id=0)
    result = SimpleNamespace(stopped_phase=1, stop_reason=1, hits=[hit], advanced_ticks=1,
                             IsPhaseBarrier=lambda: True)
    monkeypatch.setattr(backend._engine, "RunUntil", lambda *args: result)
    with pytest.raises(RuntimeError, match="unknown native FSM terminal id"):
        backend.run_until(RunLimit())
    assert backend.disarm(handle) and backend.watcher_count == 0


@pytest.mark.asyncio
async def test_stale_native_hit_cannot_complete_reused_registration(native, monkeypatch):
    backend, _ = native
    async with Execution(backend) as execution:
        async with execution.paused():
            reactor = execution.reactor
            old = reactor.register(RisingEdge(backend.clock))
            reactor.cancel(old)
            old.future.cancel()
            current = reactor.register(RisingEdge(backend.clock))
            hit = SimpleNamespace(slot=old.handle.slot, generation=old.handle.generation, kind=1,
                                  value=7, event_id=1, tick=2, phase=1, x_mask=0, source_id=0)
            result = SimpleNamespace(stopped_phase=1, stop_reason=1, hits=[hit], advanced_ticks=1,
                                     IsPhaseBarrier=lambda: True)
            with monkeypatch.context() as patch:
                patch.setattr(backend._engine, "RunUntil", lambda *args: result)
                converted = backend.run_until(RunLimit())
            assert reactor.publish(converted) == () and not current.future.done()
        event = await current.future
        assert event.kind is XEventKind.CLOCK_RISE


@pytest.mark.asyncio
async def test_native_clock_without_pin_tracking_reports_required_api():
    xspcomm = pytest.importorskip("xspcomm")

    class LegacyClock(xspcomm.XClock):
        __module__ = xspcomm.__name__

        def __getattribute__(self, name):
            if name == "HasClockPin":
                raise AttributeError(name)
            return super().__getattribute__(name)

    clock = LegacyClock(lambda _: 0)
    backend = XCommClockBackend(clock)
    pin = xspcomm.XData(1, xspcomm.XData.InOut)
    try:
        with pytest.raises(RuntimeError, match="XClock.HasClockPin"):
            backend.arm(RisingEdge(pin))
        async with Execution(backend):
            await RisingEdge(clock)
    finally:
        backend.close()


@pytest.mark.asyncio
async def test_memory_abstract_expression_failure_releases_registration():
    backend = MemoryBackend(object())
    with pytest.raises(NotImplementedError):
        async with Execution(backend):
            await CompiledTrigger("abstract", None, XExpr(), RisingEdge(backend.clock))
    assert backend.watcher_count == 0 and backend._owner is None
