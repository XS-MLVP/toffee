"""Collection must retain live observations, not merely bound cache size."""
from types import SimpleNamespace

import pytest

from xreactor import ClockCycles, Execution, RisingEdge, RunLimit, XCommClockBackend, xtrigger
from xreactor.ir import signal_expr
from xreactor.triggers import CompiledTrigger
from examples.coverage.declarative_protocol import ToyResponder
from examples.coverage.trigger_bins import Completions, ProtocolCoverage, checked_tag_one, roundtrip


@xtrigger(sample=RisingEdge("clock"))
def expected(dut, *, value):
    return signal_expr(dut.value) == value


@pytest.fixture
def native():
    x = pytest.importorskip("xspcomm")
    if not hasattr(x.XTriggerEngine, "CollectExpressions"):
        pytest.skip("requires draft xcomm expression collection")
    return x


@pytest.mark.asyncio
async def test_long_execution_collects_without_resetting_shared_coverage_fsm(native):
    toy = ToyResponder(engine="xcomm")
    toy.clock.RemoveStepRisCbByDesc("_evaluate_native")
    value = native.XData(32, native.XData.InOut)
    dut = SimpleNamespace(clock=toy.clock, value=value)
    backend = toy.backend
    group = ProtocolCoverage(instance="collection")
    group.bind(pins=toy.pins, sample=RisingEdge(toy.clock), strategy="native")
    try:
        async with Execution(backend, coverage=[group.runtime]):
            toy.pins.request.valid.Set(1)
            toy.pins.request.ready.Set(1)
            toy.pins.request.tag.Set(1)
            await ClockCycles(toy.clock, 1)
            toy.pins.request.valid.Set(0)
            for number in range(3000):
                value.Set(number)
                await expected.with_args(value=number)(dut)
                assert backend.program_cache_size <= 256
                assert backend._engine.ExpressionNodeCount() < 1800
            backend.collect_programs()
            assert backend._engine.ExpressionNodeCount() < 900
            # This program was evicted; recompilation must work after compaction.
            value.Set(0)
            await expected.with_args(value=0)(dut)
            toy.pins.response.valid.Set(1)
            toy.pins.response.ready.Set(1)
            toy.pins.response.tag.Set(1)
            toy.pins.response.data.Set(10)
            await ClockCycles(toy.clock, 1)
            assert group.completions.count(Completions.correct_data) == 1
            assert group.completions.count(Completions.bad_data) == 0
            collector = group.runtime._collector
            assert backend._engine.CoverageExecutionCount(collector._native_handle()) == 5
        assert backend._engine.ExpressionNodeCount() == backend.program_cache_size == 0
    finally:
        toy.close()


@pytest.mark.parametrize("cache_capacity", [0, 2])
def test_active_and_hit_watchers_survive_cache_eviction_and_collection(native, cache_capacity):
    clock = native.XClock(lambda _: 0)
    backend = XCommClockBackend(clock, program_cache_capacity=cache_capacity)
    value = native.XData(8, native.XData.InOut)
    dut = SimpleNamespace(clock=clock, value=value)
    first = backend.arm(expected.with_args(value=1)(dut))
    second = backend.arm(expected.with_args(value=1)(dut))
    try:
        for number in range(2, 20):
            temporary = backend.arm(expected.with_args(value=number)(dut))
            backend.disarm(temporary)
        backend.collect_programs()
        value.Set(1)
        assert len(backend.run_until(RunLimit(max_ticks=2)).hits) == 2
        # Hit watchers are occupied but not armed, and may still be rearmed.
        backend.collect_programs()
        backend.disarm(first)
        backend.collect_programs()
        assert backend.rearm(second)
        value.Set(0)
        backend.run_until(RunLimit(max_ticks=2))
        value.Set(1)
        assert len(backend.run_until(RunLimit(max_ticks=2)).hits) == 1
    finally:
        backend.disarm(second)
        backend.close()


def test_failed_lowering_and_failed_arm_leave_no_native_garbage(native):
    clock = native.XClock(lambda _: 0)
    backend = XCommClockBackend(clock, capacity=1)
    value = native.XData(8, native.XData.InOut)
    dut = SimpleNamespace(clock=clock, value=value)
    blocker = backend.arm(RisingEdge(clock))
    invalid = CompiledTrigger("invalid", None, (signal_expr(value) == 0) & (signal_expr(value) == -1),
                              sample=RisingEdge(clock))
    try:
        for number in range(64):
            with pytest.raises(ValueError):
                backend.arm(invalid)
            with pytest.raises(RuntimeError, match="capacity exhausted"):
                backend.arm(expected.with_args(value=number)(dut))
            assert backend._engine.ExpressionNodeCount() == backend.program_cache_size == 0
            assert backend.watcher_count == 1
    finally:
        backend.disarm(blocker)
        backend.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("definition", [roundtrip, checked_tag_one])
async def test_cached_sequence_and_fsm_roots_are_remapped_and_reused(native, definition):
    toy = ToyResponder(engine="xcomm")
    toy.clock.RemoveStepRisCbByDesc("_evaluate_native")
    try:
        toy.backend._engine.ExprNewConst(999)  # discarded prefix forces root relocation
        trigger = definition(toy.pins)
        handle = toy.backend.arm(trigger)
        toy.backend.disarm(handle)
        toy.backend.collect_programs()
        async with Execution(toy.backend):
            handle = toy.backend.arm(trigger)
            try:
                toy.pins.request.valid.Set(1)
                toy.pins.request.ready.Set(1)
                toy.pins.request.tag.Set(1)
                first = toy.backend.run_until(RunLimit(max_ticks=2))
                assert not first.hits
                toy.backend.collect_programs()
                toy.pins.response.valid.Set(1)
                toy.pins.response.ready.Set(1)
                toy.pins.response.tag.Set(1)
                toy.pins.response.data.Set(10)
                hits = toy.backend.run_until(RunLimit(max_ticks=2)).hits
                assert len(hits) == 1
                assert hits[0].terminal_state == ("OK" if definition is checked_tag_one else None)
            finally:
                toy.backend.disarm(handle)
    finally:
        toy.close()


@pytest.mark.asyncio
async def test_failed_coverage_attach_rolls_back_source_cache_and_bin_nodes(native, monkeypatch):
    from xreactor import Bin, CoverGroupDef, CoverPointDef
    from xreactor._coverage_runtime import _Collector

    toy = ToyResponder(engine="xcomm")
    live = ProtocolCoverage(instance="live")
    live.bind(pins=toy.pins, sample=RisingEdge(toy.clock), strategy="native")
    failing = CoverGroupDef("failed", (CoverPointDef("value", {"one": Bin.values(1)}),)).instantiate("failed")
    failing.bind(trigger=roundtrip(toy.pins), fields={"value": toy.pins.response.tag}, strategy="native")
    try:
        async with Execution(toy.backend, coverage=[live.runtime]) as execution:
            toy.backend.collect_programs()
            count = toy.backend._engine.ExpressionNodeCount()
            def reject(*args):
                raise ValueError("injected attach failure")
            with monkeypatch.context() as patch:
                patch.setattr(toy.backend._engine, "AttachCoverage", reject)
                with pytest.raises(ValueError, match="injected attach failure"):
                    _Collector(failing, execution).start()
            assert toy.backend._engine.ExpressionNodeCount() == count
            assert toy.backend.program_cache_size == 0
            assert toy.backend.watcher_count == 1
            await ClockCycles(toy.clock, 1)
    finally:
        toy.close()
