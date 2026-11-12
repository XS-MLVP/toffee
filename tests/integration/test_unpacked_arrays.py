"""Real xcomm array leaves work with Toffee without backend-specific branches."""

import ctypes
from types import SimpleNamespace

import pytest

from toffee import (
    Bin, Bundle, ClockCycles, CoverGroupDef, CoverPointDef, Execution,
    ReadyValid, ReadyValidMonitor, RisingEdge, Role, RunLimit,
    SignalArray, SyncDriver, SyncSingleCycleDriver, Value, XCommClockBackend,
    drive_ready_valid, xtrigger,
)


class Vec(ctypes.Structure):
    _fields_ = [("aval", ctypes.c_uint32), ("bval", ctypes.c_uint32)]


@pytest.fixture
def native():
    xs = pytest.importorskip("xspcomm")
    if not hasattr(xs, "XDataArray"):
        pytest.skip("requires the unreleased xcomm XDataArray API (native ABI 4)")
    return xs


@pytest.fixture(params=("mem_direct", "dpi"))
def storage(native, request):
    array = native.XDataArray(65, [3, -1], [2, 1])
    state = SimpleNamespace(array=array, kind=request.param)
    if request.param == "mem_direct":
        state.memory = (ctypes.c_uint32 * 18)()
        array.BindNativeData(ctypes.addressof(state.memory), [36, 12])
    else:
        state.values = [0] * 6
        state.masks = [0] * 6

        @ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.POINTER(Vec))
        def getter(index, output):
            for word in range(3):
                output[word].aval = (state.values[index] >> (word * 32)) & 0xffffffff
                output[word].bval = (state.masks[index] >> (word * 32)) & 0xffffffff

        @ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.POINTER(Vec))
        def setter(index, value):
            state.values[index] = sum(int(value[word].aval) << (word * 32) for word in range(3)) & ((1 << 65) - 1)
            state.masks[index] = sum(int(value[word].bval) << (word * 32) for word in range(3)) & ((1 << 65) - 1)

        state.callbacks = (getter, setter)
        array.BindDPIPtr(ctypes.cast(getter, ctypes.c_void_p).value,
                         ctypes.cast(setter, ctypes.c_void_p).value)
    state.view = SignalArray(array)
    for _, leaf in state.view.leaves():
        leaf.AsImmWrite()
    return state


def test_native_array_sampling_and_static_index_trigger(native, storage):
    array = storage.view
    expected = (1 << 64) | 0x1234
    array.drive([[expected, 2, 3], [4, 5, 6]])
    bundle = Bundle(matrix=array)
    assert bundle.sample().matrix[3][-1].as_int() == expected
    assert bundle.compile_sampler()() == bundle.sample()
    assert array[3][-1].CSelf() == storage.array.AtFlat(0).CSelf()
    assert array[3][-1].IsArrayBound()
    if storage.kind == "mem_direct":
        assert storage.memory[9] == 0x1234 and storage.memory[11] == 1
    else:
        assert storage.values[0] == expected
        storage.masks[0] = 1 << 64
        unknown = bundle.sample()
        assert unknown.matrix[3][-1].x_mask == 1 << 64
        assert bundle.compile_sampler()().matrix[3][-1] == unknown.matrix[3][-1]
        with pytest.raises(ValueError, match="X/Z"):
            unknown.as_dict()
        storage.masks[0] = 0

    @xtrigger(sample=RisingEdge("clk"))
    def selected(dut):
        return dut.matrix[3][-1] == expected

    clock = native.XClock(lambda _: 0)
    backend = XCommClockBackend(clock)
    dut = SimpleNamespace(matrix=array, clk=clock)
    try:
        direct = backend.arm(Value(array[3][-1], expected, sample=RisingEdge(clock)))
        symbolic = backend.arm(selected(dut))
        result = backend.run_until(RunLimit(max_ticks=4))
        assert len(result.hits) == 2
        backend.disarm(direct)
        backend.disarm(symbolic)
    finally:
        backend.close()


@pytest.mark.asyncio
async def test_array_leaf_reuses_native_coverage(native, storage):
    clock = native.XClock(lambda _: 0)
    backend = XCommClockBackend(clock)
    expected = (1 << 64) | 7
    storage.view[3][-1].Set(expected)
    group = CoverGroupDef("array_leaf", (CoverPointDef("word", {"wide": Bin.values(expected)}),)).instantiate("dut")
    group.bind(trigger=RisingEdge(clock), fields={"word": storage.view[3][-1]}, strategy="native")
    try:
        async with Execution(backend, coverage=[group]):
            await ClockCycles(clock, 1)
        assert group.report()["sampling_backend"] == "native"
        group.assert_coverage()
    finally:
        backend.close()


@pytest.mark.asyncio
async def test_array_driver_ownership_uses_original_native_leaves(native, storage):
    clock = native.XClock(lambda _: 0)
    backend = XCommClockBackend(clock)
    first = SyncSingleCycleDriver(clock, storage.view, idle=[[0] * 3] * 2)
    alias = SyncSingleCycleDriver(clock, SignalArray(storage.array)[3][-1], idle=0)
    try:
        async with Execution(backend):
            await first.__aenter__()
            with pytest.raises(RuntimeError, match="already have a driver"):
                await alias.__aenter__()
            first.close()
            await alias.__aenter__()
            alias.close()
    finally:
        first.close()
        alias.close()
        backend.close()


@pytest.mark.asyncio
async def test_array_payload_passes_driver_monitor_and_snapshot_boundary(native, storage):
    clock = native.XClock(lambda _: 0)
    valid = native.XData(1, native.XData.InOut)
    ready = native.XData(1, native.XData.InOut)
    ready.Set(1)
    producer = ReadyValid(clock, valid, ready, Bundle(matrix=storage.view), role=Role.PRODUCER)

    class Input(SyncDriver[dict]):
        async def _drive_one(self, request):
            return await drive_ready_valid(clock, valid, ready,
                                           lambda: producer.bits.drive(request), bits=producer.bits)

    backend = XCommClockBackend(clock)
    try:
        async with Execution(backend) as execution:
            monitor = ReadyValidMonitor(producer.monitor_view()).start(execution)
            driver = Input((valid, *(leaf for _, leaf in producer.bits.leaves())), name="array_input")
            expected = {3: {-1: (1 << 64) | 7, 0: 2, 1: 3}, 2: {-1: 4, 0: 5, 1: 6}}
            try:
                accepted = await driver.send({"matrix": expected})
                observed = await monitor.recv()
                assert observed.accepted_tick == accepted.tick
                storage.view[3][-1].Set(0)
                assert observed.value.matrix[3][-1].as_int() == (1 << 64) | 7
                assert observed.value.as_dict() == {"matrix": expected}
            finally:
                driver.close()
                await monitor.aclose()
    finally:
        backend.close()
