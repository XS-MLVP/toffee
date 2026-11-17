"""Real xcomm recursive leaves feed existing native execution, not callbacks."""

import json
from types import SimpleNamespace

import pytest

from toffee import (
    Bin, Bundle, ClockCycles, CoverGroupDef, CoverPointDef, Execution, LogicValue,
    ReadyValid, ReadyValidMonitor, RisingEdge, Role, RunLimit, SignalArray,
    SyncDriver, SyncSingleCycleDriver, Value, XCommClockBackend, drive_ready_valid, xtrigger,
)
from toffee.data import compile_sampler, normalize_data, sample_data


@pytest.fixture
def xs():
    native = pytest.importorskip("xspcomm")
    if not all(hasattr(native, name) for name in ("XView", "XStruct", "XArray")):
        pytest.skip("requires xcomm recursive views (uc-xcomm>=2.0)")
    return native


def bits(width):
    return {"kind": "bits", "packed": True, "width": width}


@pytest.fixture
def packet(xs):
    header = {"kind": "struct", "packed": True, "width": 6, "fields": [
        {"name": "tag", "offset": 1, "type": bits(5)},
        {"name": "valid", "offset": 0, "type": bits(0)},
    ]}
    words = {"kind": "array", "packed": False, "width": 0,
             "left": 3, "right": 2, "element": bits(65)}
    entry = {"kind": "struct", "packed": False, "width": 0, "fields": [
        {"name": "header", "type": header}, {"name": "payload", "type": words},
    ]}
    entries = {"kind": "array", "packed": False, "width": 0,
               "left": -1, "right": 0, "element": entry}
    layout = {"kind": "struct", "packed": False, "width": 0,
              "fields": [{"name": "entries", "type": entries}]}
    port, leaves = xs.XPort(), {}
    for index in (-1, 0):
        for member, width in (("header", 6), ("payload[3]", 65), ("payload[2]", 65)):
            path = f"packet.entries[{index}].{member}"
            leaves[path] = xs.XData(width, xs.XData.InOut)
            port.Add(path, leaves[path])
    source = xs.XView.FromPort(port, "packet", json.dumps(layout)).Struct()
    return SimpleNamespace(source=source, layout=layout, leaves=leaves, port=port)


def transaction():
    return {"entries": {
        -1: {"header": (17 << 1) | 1, "payload": {3: (1 << 64) | 7, 2: 8}},
        0: {"header": 3 << 1, "payload": {3: 9, 2: 10}},
    }}


def test_real_recursive_sources_retain_leaf_identity_and_layout(packet):
    raw = packet.source
    bound = Bundle(raw)
    bound.drive(transaction())
    assert bound.entries[-1].header.CSelf() == packet.leaves["packet.entries[-1].header"].CSelf()
    header = Bundle(bound.entries[-1].header.StructView())
    assert header.tag.CSelf() == raw["entries"][-1]["header"].StructView().Data("tag").CSelf()
    assert header.sample().as_dict() == {"tag": 17, "valid": 1}
    assert bound.entries[-1].payload[3].CSelf() == packet.leaves["packet.entries[-1].payload[3]"].CSelf()
    assert bound.sample().as_dict() == transaction()
    tree = {"packet": {"_": True, "High": -1, "Low": 0, "Layout": packet.layout}}
    rebound = Bundle.bind_tree(SimpleNamespace(packet=raw), tree)
    assert rebound.packet.entries[-1].header.CSelf() == bound.entries[-1].header.CSelf()
    assert rebound.packet.entries[-1].header.StructView().Data("tag").CSelf() == header.tag.CSelf()
    assert rebound.sample().packet == sample_data(raw)
    sampler = compile_sampler(raw)
    before = sampler()
    bound.entries[-1].payload[3].Set("0x1xxxxxxxxxxxxxxxx")
    unknown = sampler()
    assert unknown.entries[-1].payload[3].x_mask == (1 << 64) - 1
    assert unknown == sample_data(raw) and before.entries[-1].payload[3].as_int() == (1 << 64) | 7
    with pytest.raises(ValueError, match="X/Z"):
        unknown.as_dict()
    with pytest.raises(ValueError, match="X/Z"):
        bound.drive(unknown)


def test_packed_struct_and_multidimensional_array_use_the_original_parent(xs):
    header = {"kind": "struct", "packed": True, "width": 6, "fields": [
        {"name": "tag", "offset": 1, "type": bits(5)},
        {"name": "valid", "offset": 0, "type": bits(0)},
    ]}
    row = {"kind": "array", "packed": True, "width": 12, "left": -1, "right": 0, "element": header}
    layout = {"kind": "array", "packed": True, "width": 24, "left": 2, "right": 1, "element": row}
    bus = xs.XData(24, xs.XData.InOut)
    array = SignalArray(bus.ArrayView(json.dumps(layout)))
    entry = (17 << 1) | 1
    array.drive([[entry, entry], [entry, entry]])
    assert bus.U() == sum(35 << offset for offset in (0, 6, 12, 18))
    assert array.bounds == ((2, 1), (-1, 0))
    assert array.sample()[2][-1] == LogicValue(entry, 0, 6)
    assert Bundle(array[2][-1].StructView()).sample().tag == LogicValue(17, 0, 5)
    tree = {"matrix": {"_": True, "High": 23, "Low": 0, "Layout": layout}}
    bound = Bundle.bind_tree(SimpleNamespace(matrix=bus), tree)
    assert bound.matrix.sample() == array.sample()
    assert compile_sampler(bound)() == bound.sample()
    typed = normalize_data(xs.XView.FromData(bus, json.dumps(layout)))
    assert typed.sample() == array.sample()


def test_nested_leaf_static_trigger_reuses_native_expressions(xs, packet):
    bound = Bundle(packet.source)
    bound.drive(transaction())
    header = Bundle(bound.entries[-1].header.StructView())
    clock = xs.XClock(lambda _: 0)
    backend = XCommClockBackend(clock)

    @xtrigger(sample=RisingEdge("clk"))
    def selected(dut):
        return dut.header.tag == 17

    try:
        dut = SimpleNamespace(header=header, clk=clock)
        direct = backend.arm(Value(header.tag, 17, sample=RisingEdge(clock)))
        symbolic = backend.arm(selected(dut))
        assert len(backend.run_until(RunLimit(max_ticks=4)).hits) == 2
        backend.disarm(direct)
        backend.disarm(symbolic)
    finally:
        backend.close()


def test_static_native_paths_do_not_resolve_struct_fields_to_methods(xs):
    layout = {"kind": "struct", "packed": True, "width": 8, "fields": [
        {"name": "sample", "offset": 4, "type": bits(4)},
        {"name": "Data", "offset": 0, "type": bits(4)},
    ]}
    bus = xs.XData(8, xs.XData.InOut)
    source = bus.StructView(json.dumps(layout))
    bound = Bundle(source)
    bound.drive({"sample": 7, "Data": 3})
    clock = xs.XClock(lambda _: 0)
    backend = XCommClockBackend(clock)

    @xtrigger(sample=RisingEdge("clk"))
    def selected(dut):
        return (dut.packet.Data == 3) & (dut.packet.sample == 7)

    try:
        handle = backend.arm(selected(SimpleNamespace(packet=source, clk=clock)))
        assert len(backend.run_until(RunLimit(max_ticks=4)).hits) == 1
        backend.disarm(handle)
    finally:
        backend.close()


@pytest.mark.asyncio
async def test_recursive_driver_ownership_uses_the_same_xdata(xs, packet):
    clock = xs.XClock(lambda _: 0)
    backend = XCommClockBackend(clock)
    first = SyncSingleCycleDriver(clock, packet.source, idle=transaction())
    alias = SyncSingleCycleDriver(clock, packet.source["entries"][-1]["header"], idle=0)
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
async def test_nested_wide_leaf_has_native_coverage(xs, packet):
    clock = xs.XClock(lambda _: 0)
    backend = XCommClockBackend(clock)
    bound = Bundle(packet.source)
    expected = (1 << 64) | 7
    bound.entries[-1].payload[3].Set(expected)
    group = CoverGroupDef("aggregate_leaf", (
        CoverPointDef("word", {"wide": Bin.values(expected)}),
    )).instantiate("dut")
    group.bind(trigger=RisingEdge(clock), fields={"word": bound.entries[-1].payload[3]}, strategy="native")
    try:
        async with Execution(backend, coverage=[group]):
            await ClockCycles(clock, 1)
        assert group.report()["sampling_backend"] == "native"
        group.assert_coverage()
    finally:
        backend.close()


@pytest.mark.parametrize("kind", ("struct", "array"))
def test_associated_packed_data_and_explicit_views_share_the_original_value(xs, kind):
    if kind == "struct":
        layout = {"kind": "struct", "packed": True, "width": 8, "fields": [
            {"name": "tag", "offset": 4, "type": bits(4)},
            {"name": "payload", "offset": 0, "type": bits(4)},
        ]}
        values = {"tag": 7, "payload": 2}
    else:
        layout = {"kind": "array", "packed": True, "width": 8,
                  "left": 3, "right": 2, "element": bits(4)}
        values = {3: 7, 2: 2}
    signal = xs.XData(8, xs.XData.InOut)
    signal.SetLayout(json.dumps(layout))
    assert normalize_data(signal) is signal
    whole = Bundle(packet=signal)
    whole.drive({"packet": 0xa5})
    view = signal.StructView() if kind == "struct" else signal.ArrayView()
    interpreted = normalize_data(view)
    interpreted.drive(values)
    assert signal.U() == 0x72
    assert whole.sample().packet == LogicValue(0x72, 0, 8)
    assert compile_sampler(whole)() == whole.sample()
    first = "tag" if kind == "struct" else 3
    assert interpreted[first].CSelf() == view.Data(first).CSelf()
    with pytest.raises(ValueError, match="scalar drive value"):
        whole.drive({"packet": values})


@pytest.mark.asyncio
async def test_recursive_payload_crosses_driver_monitor_snapshot_boundary(xs, packet):
    clock = xs.XClock(lambda _: 0)
    valid, ready = xs.XData(1, xs.XData.InOut), xs.XData(1, xs.XData.InOut)
    ready.Set(1)
    interface = ReadyValid(clock, valid, ready, packet.source, role=Role.PRODUCER)

    class Input(SyncDriver[dict]):
        async def _drive_one(self, request):
            return await drive_ready_valid(clock, valid, ready,
                lambda: interface.bits.drive(request), bits=interface.bits)

    backend = XCommClockBackend(clock)
    try:
        async with Execution(backend) as execution:
            monitor = ReadyValidMonitor(interface.monitor_view()).start(execution)
            driver = Input((valid, *(leaf for _, leaf in interface.bits.leaves())), name="aggregate_input")
            try:
                accepted = await driver.send(transaction())
                observed = await monitor.recv()
                assert observed.accepted_tick == accepted.tick
                interface.bits.entries[-1].payload[3].Set(0)
                assert observed.value.as_dict() == transaction()
            finally:
                driver.close()
                await monitor.aclose()
    finally:
        backend.close()
