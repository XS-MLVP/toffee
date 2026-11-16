"""Bind real Picker port metadata without inferring protocols.

The frozen fixtures under ``fixtures/`` are verbatim ``picker config
--section metadata`` output; ``fixtures/README.md`` records the exact Picker
version and commands.  The tests that need a live simulator re-export
``aggregate_views.sv`` and import the generated DUT.  They are skipped when
the Picker binary or source file is unavailable; every layout/parsing test
still runs from the frozen metadata.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from toffee import (
    Bundle,
    LogicValue,
    MemoryBackend,
    Execution,
    PackedLayout,
    PackedView,
    PortLayout,
    PortView,
    ReadyValid,
    Role,
    SignalArray,
    drive_ready_valid,
)
from toffee.data import iter_data_leaves, normalize_data

FIXTURES = Path(__file__).with_name("fixtures")
DEFAULT_PICKER = Path("/home/xyl/picker/build/bin/picker")
DEFAULT_SOURCE = Path("/home/xyl/picker/test/data/aggregate_views.sv")


def _load_metadata(name: str) -> dict:
    with (FIXTURES / name).open(encoding="utf-8") as handle:
        return json.load(handle)


AGGREGATE_METADATA = _load_metadata(
    "picker_aggregate_views_metadata.json"
)
COLLISION_METADATA = _load_metadata("picker_collisions_metadata.json")
PACKED_STRUCT_ARRAY_METADATA = _load_metadata(
    "picker_packed_struct_array_metadata.json"
)


def _port(metadata: dict, *parts: str) -> dict:
    node = metadata["ports"][parts[0]]
    for part in parts[1:]:
        node = node[part]
    return node


def _leaf_from(node: dict) -> dict:
    """Copy the signal-tree leaf keys Picker emitted for one port."""
    return {
        key: node[key]
        for key in ("_", "Pin", "High", "Low", "Unpacked", "Layout")
        if key in node
    }


# ---------------------------------------------------------------------------
# Lightweight signal doubles used by the non-simulator tests.
# ---------------------------------------------------------------------------


class Signal:
    def __init__(self, value=0, *, width=8, x_mask=0, name=""):
        self.value = value
        self.width = width
        self.x_mask = x_mask
        self.mName = name

    def W(self):
        return self.width

    def U(self):
        return self.value

    def XMask(self):
        return self.x_mask

    def Set(self, value):
        self.value = value


class Slice(Signal):
    def __init__(self, parent, low, width, name=""):
        super().__init__(0, width=width, name=name)
        self.parent = parent
        self.low = low

    def U(self):
        return (self.parent.U() >> self.low) & ((1 << self.width) - 1)

    def Set(self, value):
        mask = ((1 << self.width) - 1) << self.low
        self.parent.Set(
            (self.parent.U() & ~mask) | ((value << self.low) & mask)
        )

    def XMask(self):
        return (self.parent.XMask() >> self.low) & ((1 << self.width) - 1)


class Bus(Signal):
    def SubDataRef(self, low, width, name=""):
        return Slice(self, low, width, name)


class RtlArray:
    """XArray-shaped double: ``At`` takes RTL indices."""

    def __init__(self, bounds, elements):
        self._bounds = (tuple(bounds),)
        self._elements = dict(elements)

    @property
    def bounds(self):
        return self._bounds

    def LeftBounds(self):
        return (self._bounds[0][0],)

    def RightBounds(self):
        return (self._bounds[0][1],)

    def __getitem__(self, index):
        return self._elements[index]

    def At(self, index):
        return self._elements[index]

    def IsPacked(self):
        return False

    def PackedData(self):
        raise RuntimeError("unpacked array")


class PositionalArray:
    """Array-shaped double whose ``__getitem__`` is 0-based positional."""

    def __init__(self, bounds, elements):
        self.bounds = (tuple(bounds),)
        self._elements = list(elements)
        self.indices = tuple(
            range(
                bounds[0],
                bounds[1] + (1 if bounds[0] <= bounds[1] else -1),
                1 if bounds[0] <= bounds[1] else -1,
            )
        )

    def __getitem__(self, position):
        return self._elements[position]

    def __len__(self):
        return len(self._elements)

    def IsPacked(self):
        return False

    def PackedData(self):
        raise RuntimeError("unpacked array")


class XArray:
    """Small stand-in for Picker's XArray container."""

    def __init__(self, bounds, elements, *, packed=False):
        self.bounds = (tuple(bounds),)
        self._elements = dict(elements)
        self._packed = packed

    def IsPacked(self):
        return self._packed

    def At(self, index):
        return self._elements[index]

    def Data(self, index):
        return self._elements[index]

    def PackedData(self):
        if not self._packed:
            raise RuntimeError("unpacked array")
        return self._elements["packed"]


class XStruct:
    def __init__(self, fields):
        self._fields = dict(fields)

    def Names(self):
        return tuple(self._fields)

    def Data(self, name):
        item = self._fields[name]
        if not isinstance(item, Signal):
            raise TypeError(name)
        return item

    def Struct(self, name):
        item = self._fields[name]
        if not isinstance(item, XStruct):
            raise TypeError(name)
        return item

    def Array(self, name):
        item = self._fields[name]
        if not isinstance(item, XArray):
            raise TypeError(name)
        return item


class XView:
    def __init__(self, kind, node):
        self.kind = kind
        self.node = node

    def Kind(self):
        return self.kind

    def Data(self):
        if self.kind != "bits":
            raise TypeError(self.kind)
        return self.node

    def Array(self):
        if self.kind != "array":
            raise TypeError(self.kind)
        return self.node

    def Struct(self):
        if self.kind != "struct":
            raise TypeError(self.kind)
        return self.node


class Dut:
    pass


# ---------------------------------------------------------------------------
# Frozen metadata shape checks.
# ---------------------------------------------------------------------------


def test_real_picker_matrix_layout_is_a_packed_8_bit_leaf():
    node = _port(AGGREGATE_METADATA, "matrix")
    assert (node["High"], node["Low"], node["_"], node["Pin"]) == (
        7,
        0,
        True,
        "input",
    )
    layout = PortLayout.from_json(node["Layout"])
    assert (layout.kind, layout.packed, layout.width) == (
        "array",
        True,
        8,
    )
    assert (layout.left, layout.right) == (1, 0)
    assert layout.indices == (1, 0)
    assert layout.signed is True
    assert layout.element is not None
    assert (layout.element.kind, layout.element.width) == ("array", 4)
    assert (layout.element.left, layout.element.right) == (-2, 1)
    assert layout.element.indices == (-2, -1, 0, 1)
    assert layout.element.element is not None
    assert (layout.element.element.kind, layout.element.element.width) == (
        "bits",
        1,
    )
    assert layout.element.element.signed is True
    # Wrapped/older metadata may carry the same document as a JSON string.
    assert PortLayout.from_json(json.dumps(node["Layout"])) == layout


def test_real_picker_packed_struct_layout_uses_lsb_bit_offsets():
    layout = PortLayout.from_json(
        _port(AGGREGATE_METADATA, "packed", "in")["Layout"]
    )
    assert (layout.kind, layout.packed, layout.width) == (
        "struct",
        True,
        6,
    )
    assert [field.name for field in layout.fields] == ["tag", "valid"]
    tag, valid = layout.fields
    assert (tag.offset, tag.width, tag.signed) == (1, 5, True)
    assert (valid.offset, valid.width, valid.signed) == (0, 1, False)
    assert tag.type.kind == "array"
    assert tag.type.indices == (0, 1, 2, 3, 4)
    assert tag.type.element is not None
    assert (tag.type.element.kind, tag.type.element.width) == ("bits", 1)
    # A one-bit scalar is width 0 in Picker metadata but width 1 in the view.
    assert valid.type.kind == "bits"
    assert valid.type.width == 1


def test_real_picker_unpacked_struct_layout_nests_arrays_and_packed_fields():
    layout = PortLayout.from_json(
        _port(AGGREGATE_METADATA, "record", "in")["Layout"]
    )
    assert (layout.kind, layout.packed, layout.width) == (
        "struct",
        False,
        0,
    )
    assert [field.name for field in layout.fields] == ["enabled", "entries"]
    enabled, entries = layout.fields
    assert enabled.offset is None
    assert enabled.width == 1
    assert entries.offset is None
    assert (entries.type.kind, entries.type.packed, entries.type.width) == (
        "array",
        False,
        0,
    )
    assert (entries.type.left, entries.type.right) == (-1, 0)
    entry = entries.type.element
    assert entry is not None
    assert (entry.kind, entry.packed, entry.width) == ("struct", False, 0)
    assert [field.name for field in entry.fields] == [
        "header",
        "payload",
        "flag",
    ]
    header, payload, flag = entry.fields
    assert (header.type.kind, header.type.width) == ("struct", 6)
    assert (header.type.fields[0].name, header.type.fields[0].width) == (
        "tag",
        5,
    )
    assert (payload.type.kind, payload.type.left, payload.type.right) == (
        "array",
        3,
        2,
    )
    assert payload.type.element is not None
    assert (payload.type.element.kind, payload.type.element.width) == (
        "array",
        65,
    )
    assert flag.width == 1


def test_real_picker_packed_records_is_an_unpacked_array_of_packed_structs():
    node = _port(AGGREGATE_METADATA, "packed", "records")
    assert (node["High"], node["Low"], node["_"], node["Pin"]) == (
        -1,
        0,
        True,
        "input",
    )
    layout = PortLayout.from_json(node["Layout"])
    assert (layout.kind, layout.packed, layout.width) == (
        "array",
        False,
        0,
    )
    assert (layout.left, layout.right) == (2, 1)
    assert layout.element is not None
    assert (layout.element.kind, layout.element.packed) == ("struct", True)
    assert layout.element.width == 6


def test_real_picker_packed_array_of_struct_keeps_element_views():
    node = _port(PACKED_STRUCT_ARRAY_METADATA, "arr")
    layout = PortLayout.from_json(node["Layout"])
    assert (layout.kind, layout.packed, layout.width) == (
        "array",
        True,
        15,
    )
    assert (layout.left, layout.right) == (2, 0)
    assert layout.element is not None
    assert (layout.element.kind, layout.element.packed) == ("struct", True)
    assert layout.element.width == 5
    assert [field.name for field in layout.element.fields] == ["a", "b"]
    assert layout.element.fields[0].type.width == 4
    assert layout.element.fields[1].type.width == 1


# ---------------------------------------------------------------------------
# Projection of real layouts onto live views.
# ---------------------------------------------------------------------------


def test_real_packed_struct_layout_binds_fields_and_drives_integers():
    dut = Dut()
    dut.io_req_bits = Bus(width=6)
    tree = {"bits": _leaf_from(_port(AGGREGATE_METADATA, "packed", "in"))}

    bundle = Bundle.bind_tree(dut, tree, source_prefix="io_req")
    view = bundle.bits

    assert isinstance(view, PortView)
    assert isinstance(view.node, PackedView)
    assert view.layout is not None
    assert (view.layout.kind, view.layout.width) == ("struct", 6)
    assert tuple(view.fields) == ("tag", "valid")
    assert view.fields["tag"].W() == 5
    assert view.fields["valid"].W() == 1
    assert view.layout.fields[0].signed is True

    view.drive({"tag": 0b10110, "valid": 1})
    assert dut.io_req_bits.U() == 0b101101
    assert view.sample().as_dict() == {"tag": 0b10110, "valid": 1}

    view.fields["tag"].Set(0b01010)
    view.fields["valid"].Set(0)
    assert dut.io_req_bits.U() == 0b010100
    assert [path for path, _ in view.leaves()] == [
        "bundle.bits.tag",
        "bundle.bits.valid",
    ]


def test_real_packed_struct_layout_falls_back_to_bit_slices_without_native_subdata():
    # Plain Python signals have no SubDataRef; _SignalSlices keeps the packed
    # view usable while real XData always takes the native SubDataRef path.
    dut = Dut()
    dut.io_req_bits = Signal(width=6)
    tree = {"bits": _leaf_from(_port(AGGREGATE_METADATA, "packed", "in"))}

    view = Bundle.bind_tree(dut, tree, source_prefix="io_req").bits
    view.drive({"tag": 0b10110, "valid": 1})
    assert dut.io_req_bits.U() == 0b101101
    assert view.tag.U() == 0b10110
    assert view.valid.U() == 1


def test_real_packed_matrix_layout_is_one_leaf_and_preserves_bit_order():
    dut = Dut()
    dut.io_matrix = Bus(width=8)
    tree = {"matrix": _leaf_from(_port(AGGREGATE_METADATA, "matrix"))}

    view = Bundle.bind_tree(dut, tree, source_prefix="io").matrix

    assert isinstance(view, PortView)
    # The 2x4 packed array is one packed value, not 2x4 independent bits.
    assert view.node is dut.io_matrix
    assert not isinstance(view.node, PackedView)
    assert view.W() == 8
    assert view.layout.width == 8
    assert view.layout.indices == (1, 0)
    assert view.layout.element.indices == (-2, -1, 0, 1)
    assert [path for path, _ in view.leaves()] == ["bundle.matrix"]
    with pytest.raises(AttributeError, match="not an indexable array"):
        _ = view.indices

    view.drive(0b10010110)
    assert dut.io_matrix.U() == 0b10010110
    sampled = view.sample()
    assert isinstance(sampled, LogicValue)
    assert sampled.as_int() == 0b10010110
    assert sampled.width == 8


def test_real_packed_array_of_struct_keeps_fields_and_msb_order():
    dut = Dut()
    dut.io_arr = Bus(width=15)
    tree = {
        "arr": _leaf_from(_port(PACKED_STRUCT_ARRAY_METADATA, "arr"))
    }

    view = Bundle.bind_tree(dut, tree, source_prefix="io").arr

    assert isinstance(view.node, PackedView)
    assert view.indices == (2, 1, 0)
    assert view[2].a.W() == 4
    assert view[2].b.W() == 1
    assert [path for path, _ in view.leaves()] == [
        "bundle.arr[2].a",
        "bundle.arr[2].b",
        "bundle.arr[1].a",
        "bundle.arr[1].b",
        "bundle.arr[0].a",
        "bundle.arr[0].b",
    ]

    values = {
        2: {"a": 0xA, "b": 1},
        1: {"a": 0x5, "b": 0},
        0: {"a": 0xF, "b": 1},
    }
    view.drive(values)
    expected = (0b10101 << 10) | (0b01010 << 5) | 0b11111
    assert dut.io_arr.U() == expected
    assert view[2].sample().as_dict() == {"a": 0xA, "b": 1}
    assert view[1].sample().as_dict() == {"a": 0x5, "b": 0}
    assert view[0].sample().as_dict() == {"a": 0xF, "b": 1}


def test_packed_view_array_leaf_paths_and_indexing_use_rtl_indices():
    bus = Bus(width=16)
    layout = PackedLayout.array(
        2,
        PackedLayout.bits(8),
        lsb_first=False,
        left=1,
        right=0,
    )
    view = PackedView(bus, layout, name="word")

    assert view.indices == (1, 0)
    assert view.bounds == ((1, 0),)
    assert view.shape == (2,)
    assert [path for path, _ in view.leaves()] == [
        "word[1]",
        "word[0]",
    ]
    view.drive({1: 0xAA, 0: 0x55})
    assert bus.U() == 0xAA55
    assert view[1].U() == 0xAA
    assert view[0].U() == 0x55
    with pytest.raises(IndexError, match="RTL array index"):
        _ = view[2]
    with pytest.raises(IndexError, match="RTL array index"):
        _ = view[-1]


def test_packed_view_lsb_first_with_rtl_bounds_maps_right_index_to_lsb():
    bus = Bus(width=16)
    layout = PackedLayout.array(
        2,
        PackedLayout.bits(8),
        lsb_first=True,
        left=1,
        right=0,
    )
    view = PackedView(bus, layout, name="word")

    assert view.indices == (0, 1)
    view.drive({0: 0x55, 1: 0xAA})
    assert bus.U() == 0xAA55
    assert view[0].U() == 0x55
    assert view[1].U() == 0xAA
    assert [path for path, _ in view.leaves()] == [
        "word[0]",
        "word[1]",
    ]


# ---------------------------------------------------------------------------
# RTL index semantics for SignalArray under both access styles.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bounds", [(3, 2), (-1, 0)])
def test_signal_array_uses_rtl_indices_for_rtl_backed_arrays(bounds):
    left, right = bounds
    signals = {
        left: Signal(width=8),
        right: Signal(width=8),
    }
    view = SignalArray(RtlArray(bounds, signals), path="lane")

    assert view.bounds == (bounds,)
    assert view.indices == bounds
    assert list(view) == [signals[left], signals[right]]
    assert view[left] is signals[left]
    assert view[right] is signals[right]
    assert [path for path, _ in view.leaves()] == [
        f"lane[{left}]",
        f"lane[{right}]",
    ]

    view[left].Set(0x11)
    view[right].Set(0x22)
    assert tuple(value.as_int() for value in view.sample()) == (0x11, 0x22)
    view.drive({left: 0x33, right: 0x44})
    assert (signals[left].value, signals[right].value) == (0x33, 0x44)
    view.drive([0x55, 0x66])
    assert (signals[left].value, signals[right].value) == (0x55, 0x66)
    with pytest.raises(IndexError):
        _ = view[right + 1 if right >= left else left + 1]


@pytest.mark.parametrize("bounds", [(3, 2), (-1, 0)])
def test_signal_array_translates_rtl_indices_for_positional_arrays(bounds):
    left, right = bounds
    signals = [Signal(width=8), Signal(width=8)]
    # Positional storage is in declaration order: left element first.
    view = SignalArray(PositionalArray(bounds, signals), path="lane")

    assert view.indices == bounds
    assert list(view) == signals
    assert view[left] is signals[0]
    assert view[right] is signals[1]
    assert [path for path, _ in view.leaves()] == [
        f"lane[{left}]",
        f"lane[{right}]",
    ]

    view.drive({left: 0x77, right: 0x88})
    assert (signals[0].value, signals[1].value) == (0x77, 0x88)
    view.drive([0x99, 0xAA])
    assert (signals[0].value, signals[1].value) == (0x99, 0xAA)


# ---------------------------------------------------------------------------
# Real Picker signal-tree collisions (data/data_0 and req/req_valid).
# ---------------------------------------------------------------------------


def test_real_picker_prefix_collisions_bind_leaf_and_child_together():
    dut = Dut()
    dut.data = Signal(width=1)
    dut.data_0 = Signal(width=4)
    dut.data_out = Signal(width=1)
    dut.data_0_out = Signal(width=4)
    dut.req = Signal(width=1)
    dut.req_valid = Signal(width=1)
    dut.req_out = Signal(width=1)
    dut.req_valid_out = Signal(width=1)

    bundle = Bundle.bind_tree(dut, COLLISION_METADATA["ports"])

    assert list(bundle) == [
        "data",
        "data_0",
        "data_0_out",
        "data_out",
        "req",
        "req_out",
        "req_valid",
        "req_valid_out",
    ]
    assert bundle.data is dut.data
    assert bundle.data_0 is dut.data_0
    assert bundle.data_0_out is dut.data_0_out
    assert bundle.data_out is dut.data_out
    assert bundle.req is dut.req
    assert bundle.req_out is dut.req_out
    assert bundle.req_valid is dut.req_valid
    assert bundle.req_valid_out is dut.req_valid_out
    assert [path for path, _ in bundle.leaves()] == [
        "bundle.data",
        "bundle.data_0",
        "bundle.data_0_out",
        "bundle.data_out",
        "bundle.req",
        "bundle.req_out",
        "bundle.req_valid",
        "bundle.req_valid_out",
    ]

    bundle.drive(
        {
            "data": 1,
            "data_0": 0xA,
            "data_0_out": 0x5,
            "data_out": 0,
            "req": 0,
            "req_out": 1,
            "req_valid": 1,
            "req_valid_out": 0,
        }
    )
    assert (dut.data.U(), dut.data_0.U(), dut.data_0_out.U()) == (
        1,
        0xA,
        0x5,
    )
    assert (dut.data_out.U(), dut.req.U()) == (0, 0)
    assert (dut.req_out.U(), dut.req_valid.U(), dut.req_valid_out.U()) == (
        1,
        1,
        0,
    )
    assert bundle.sample().as_dict() == {
        "data": 1,
        "data_0": 0xA,
        "data_0_out": 0x5,
        "data_out": 0,
        "req": 0,
        "req_out": 1,
        "req_valid": 1,
        "req_valid_out": 0,
    }


def test_bundle_accepts_picker_xarray_xstruct_and_xview_containers():
    first = Signal(1, width=8)
    second = Signal(2, width=8)
    struct = XStruct({"first": first, "second": second})
    array = XArray((0, 1), {0: first, 1: second})
    packed_data = Signal(0xA5, width=8)
    packed_array = XArray((1, 0), {"packed": packed_data}, packed=True)

    bundle = Bundle(
        array=array,
        struct=struct,
        bits=XView("bits", first),
        array_view=XView("array", array),
        struct_view=XView("struct", struct),
        packed_view=XView("array", packed_array),
    )

    assert isinstance(bundle.array, SignalArray)
    assert isinstance(bundle.struct, Bundle)
    assert bundle.bits is first
    assert isinstance(bundle.array_view, SignalArray)
    assert isinstance(bundle.struct_view, Bundle)
    # A packed XArray is one packed value, not 8 independent one-bit leaves.
    assert bundle.packed_view is packed_data
    assert bundle.struct.first is first
    assert bundle.array[1] is second
    assert bundle.sample().as_dict() == {
        "array": (1, 2),
        "struct": {"first": 1, "second": 2},
        "bits": 1,
        "array_view": (1, 2),
        "struct_view": {"first": 1, "second": 2},
        "packed_view": 0xA5,
    }

    bundle.array.drive([5, 6])
    bundle.struct.drive({"first": 7, "second": 8})
    assert (first.value, second.value) == (7, 8)


# ---------------------------------------------------------------------------
# ReadyValid accepts every view type it claims to accept.
# ---------------------------------------------------------------------------


def _ready_valid_dut(bits_object):
    dut = Dut()
    dut.clk = object()
    dut.io_req_valid = Signal(0, width=1)
    dut.io_req_ready = Signal(1, width=1)
    dut.io_req_bits = bits_object
    return dut


def _packed_bits(bus):
    return Bundle.bind_tree(
        _ready_valid_dut(bus),
        {
            "valid": {"_": True, "High": 0, "Low": 0},
            "ready": {"_": True, "High": 0, "Low": 0},
            "bits": _leaf_from(_port(AGGREGATE_METADATA, "packed", "in")),
        },
        source_prefix="io_req",
    ).bits


def _port_view_bits():
    dut = _ready_valid_dut(Bus(width=8))
    return Bundle.bind_tree(
        dut,
        {
            "valid": {"_": True, "High": 0, "Low": 0},
            "ready": {"_": True, "High": 0, "Low": 0},
            "bits": _leaf_from(_port(AGGREGATE_METADATA, "matrix")),
        },
        source_prefix="io_req",
    ).bits


def _signal_array_bits():
    dut = _ready_valid_dut(
        RtlArray((1, 0), {1: Signal(width=8), 0: Signal(width=8)})
    )
    return Bundle.bind_tree(
        dut,
        {
            "valid": {"_": True, "High": 0, "Low": 0},
            "ready": {"_": True, "High": 0, "Low": 0},
            "bits": {"_": True, "High": 7, "Low": 0, "Unpacked": [[1, 0]]},
        },
        source_prefix="io_req",
    ).bits


def _bundle_bits():
    dut = _ready_valid_dut(object())
    dut.io_req_bits_first = Signal(width=4)
    dut.io_req_bits_second = Signal(width=4)
    return Bundle.bind_tree(
        dut,
        {
            "valid": {"_": True, "High": 0, "Low": 0},
            "ready": {"_": True, "High": 0, "Low": 0},
            "bits": {
                "first": {"_": True, "High": 3, "Low": 0},
                "second": {"_": True, "High": 3, "Low": 0},
            },
        },
        source_prefix="io_req",
    ).bits


def test_ready_valid_bind_tree_accepts_every_advertised_bits_view():
    packed = _packed_bits(Bus(width=6))
    port = _port_view_bits()
    array = _signal_array_bits()
    bundle = _bundle_bits()

    assert isinstance(packed, PortView)
    assert isinstance(packed.node, PackedView)
    assert isinstance(port, PortView)
    assert isinstance(array, PortView)
    assert isinstance(array.node, SignalArray)
    assert isinstance(bundle, Bundle)

    for node in (packed, port, array, bundle):
        assert normalize_data(node, "bits") is node
        assert tuple(iter_data_leaves(node, "bits"))


def test_ready_valid_accepts_raw_packed_view_and_signal_array_nodes():
    dut = _ready_valid_dut(
        _packed_bits(Bus(width=6)).node
    )
    raw_packed = Bundle.bind_tree(
        dut,
        {
            "valid": {"_": True, "High": 0, "Low": 0},
            "ready": {"_": True, "High": 0, "Low": 0},
            "bits": {"_": True},
        },
        source_prefix="io_req",
    )
    assert isinstance(raw_packed.bits, PackedView)
    interface = ReadyValid(
        dut.clk,
        raw_packed.valid,
        raw_packed.ready,
        raw_packed.bits,
        name="io.req",
    )
    assert isinstance(interface.bits, PackedView)

    signals = RtlArray((1, 0), {1: Signal(width=8), 0: Signal(width=8)})
    dut2 = _ready_valid_dut(signals)
    raw_array = Bundle.bind_tree(
        dut2,
        {
            "valid": {"_": True, "High": 0, "Low": 0},
            "ready": {"_": True, "High": 0, "Low": 0},
            "bits": {"_": True},
        },
        source_prefix="io_req",
    )
    assert isinstance(raw_array.bits, SignalArray)
    interface = ReadyValid(
        dut2.clk,
        raw_array.valid,
        raw_array.ready,
        raw_array.bits,
        name="io.req",
    )
    assert isinstance(interface.bits, SignalArray)


@pytest.mark.asyncio
async def test_drive_ready_valid_walks_every_accepted_bits_view():
    ready = Signal(1, width=1)
    nodes = (
        _packed_bits(Bus(width=6)),
        _port_view_bits(),
        _signal_array_bits(),
        _bundle_bits(),
    )
    for bits in nodes:
        dut = _ready_valid_dut(bits)
        dut.io_req_ready = ready
        interface = ReadyValid(
            dut.clk,
            dut.io_req_valid,
            dut.io_req_ready,
            bits,
            role=Role.MONITOR,
            name="io.req",
        )
        backend = MemoryBackend(dut.clk)
        async with Execution(backend):
            event = await drive_ready_valid(
                dut.clk,
                interface.valid,
                interface.ready,
                lambda: None,
                bits=interface.bits,
            )
        assert event is not None
        assert interface.valid.U() == 0


def test_ready_valid_accepts_real_packed_struct_layout():
    dut = _ready_valid_dut(Bus(width=6))
    interface = ReadyValid.bind_tree(
        dut,
        {
            "valid": {"_": True, "High": 0, "Low": 0},
            "ready": {"_": True, "High": 0, "Low": 0},
            "bits": _leaf_from(_port(AGGREGATE_METADATA, "packed", "in")),
        },
        clock=dut.clk,
        source_prefix="io_req",
        role=Role.MONITOR,
        name="io.req",
    )

    assert isinstance(interface.bits, PortView)
    assert isinstance(interface.bits.node, PackedView)
    assert interface.bits.tag.W() == 5
    interface.bits.drive({"tag": 0b10110, "valid": 1})
    assert dut.io_req_bits.U() == 0b101101


# ---------------------------------------------------------------------------
# Error paths.
# ---------------------------------------------------------------------------


def test_layout_rejects_unknown_kind_and_malformed_documents():
    with pytest.raises(ValueError, match="unsupported aggregate metadata"):
        PortLayout.from_json({"kind": "union", "packed": True, "width": 8})
    with pytest.raises(ValueError, match="invalid Layout JSON"):
        PortLayout.from_json("{not json")
    with pytest.raises(TypeError, match="mapping or a JSON string"):
        PortLayout.from_json(42)
    with pytest.raises(ValueError, match="Layout must be a JSON object"):
        PortLayout.from_json("[1, 2]")
    with pytest.raises(ValueError, match="layout nesting exceeds 64"):
        nested = {
            "kind": "bits",
            "packed": True,
            "width": 1,
        }
        for _ in range(70):
            nested = {
                "kind": "array",
                "packed": True,
                "width": 1,
                "left": 0,
                "right": 0,
                "element": nested,
            }
        PortLayout.from_json(nested)


def test_layout_rejects_inconsistent_packed_array_width():
    bad = {
        "kind": "array",
        "packed": True,
        "width": 7,
        "left": 1,
        "right": 0,
        "element": {"kind": "bits", "packed": True, "width": 4},
    }
    with pytest.raises(ValueError, match="does not match 2 elements"):
        PortLayout.from_json(bad)


def test_bind_tree_rejects_layout_width_mismatch():
    dut = Dut()
    dut.io_req_bits = Bus(width=5)
    tree = {"bits": _leaf_from(_port(AGGREGATE_METADATA, "packed", "in"))}
    with pytest.raises(ValueError, match="packed layout width 6"):
        Bundle.bind_tree(dut, tree, source_prefix="io_req")


def test_packed_layout_array_bounds_must_match_count():
    with pytest.raises(ValueError, match="does not match RTL bounds"):
        PackedLayout.array(
            2,
            PackedLayout.bits(8),
            left=2,
            right=0,
        )
    with pytest.raises(ValueError, match="both left and right"):
        PackedLayout.array(2, PackedLayout.bits(8), left=1)


def test_unsupported_xview_kind_is_named_in_the_error():
    with pytest.raises(ValueError, match="XView kind 'queue'"):
        Bundle(bad=XView("queue", object()))


# ---------------------------------------------------------------------------
# Live Picker export (skipped without the binary and source).
# ---------------------------------------------------------------------------


def _load_generated_module(directory: Path, name: str):
    path = str(directory)
    sys.path.insert(0, path)
    try:
        spec = importlib.util.spec_from_file_location(
            name,
            directory / "__init__.py",
            submodule_search_locations=[path],
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        try:
            sys.path.remove(path)
        except ValueError:
            pass


def _export_picker_module(
    tmp_path_factory,
    *,
    source: Path,
    source_name: str,
    target_name: str,
    label: str,
):
    picker = Path(os.environ.get("PICKER_BIN", DEFAULT_PICKER))
    if not picker.is_file() or not source.is_file():
        pytest.skip(
            f"Picker export unavailable: binary={picker}, source={source}"
        )
    out = tmp_path_factory.mktemp(label) / "out"
    export = subprocess.run(
        [
            str(picker),
            "export",
            str(source),
            "--sim",
            "verilator",
            "--sname",
            source_name,
            "--tname",
            target_name,
            "--tdir",
            str(out),
        ],
        capture_output=True,
        text=True,
        timeout=900,
    )
    if export.returncode != 0:
        pytest.skip(
            "Picker export failed (simulator toolchain unavailable): "
            f"{export.stderr[-2000:]}"
        )
    config = subprocess.run(
        [
            str(picker),
            "config",
            str(out / f"{target_name}.xscfg"),
            "--section",
            "metadata",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert config.returncode == 0, config.stderr
    metadata = json.loads(config.stdout)
    module_name = "toffee_picker_" + label.replace("-", "_") + "_dut"
    module = _load_generated_module(out, module_name)
    return SimpleNamespace(
        module=module,
        metadata=metadata,
        directory=out,
    )


@pytest.fixture(scope="session")
def picker_export(tmp_path_factory):
    source = Path(
        os.environ.get("PICKER_AGGREGATE_VIEWS_SV", DEFAULT_SOURCE)
    )
    return _export_picker_module(
        tmp_path_factory,
        source=source,
        source_name="AggregatePorts",
        target_name="AggDUT",
        label="aggregate",
    )


@pytest.fixture(scope="session")
def picker_struct_array_export(tmp_path_factory):
    source = Path(
        os.environ.get(
            "PICKER_PACKED_STRUCT_ARRAY_SV",
            FIXTURES / "picker_packed_struct_array.sv",
        )
    )
    return _export_picker_module(
        tmp_path_factory,
        source=source,
        source_name="M",
        target_name="PDUT",
        label="packed-struct-array",
    )


@pytest.fixture
def picker_dut(picker_export):
    dut = picker_export.module.DUTAggDUT()
    try:
        yield dut
    finally:
        dut.Finish()


@pytest.fixture
def picker_struct_array_dut(picker_struct_array_export):
    dut = picker_struct_array_export.module.DUTPDUT()
    try:
        yield dut
    finally:
        dut.Finish()


def test_regenerated_picker_metadata_matches_frozen_fixture(picker_export):
    assert picker_export.metadata["ports"] == AGGREGATE_METADATA["ports"]
    assert (
        picker_export.metadata["generator"]["simulator"]
        == AGGREGATE_METADATA["generator"]["simulator"]
    )


def test_real_dut_binds_all_aggregate_ports(picker_export, picker_dut):
    bundle = Bundle.bind_tree(picker_dut, picker_export.metadata["ports"])

    assert list(bundle) == ["matrix", "matrix_out", "packed", "record"]
    assert bundle.matrix.W() == 8
    assert not isinstance(bundle.matrix.node, PackedView)
    assert bundle.packed.in_.tag.W() == 5
    assert bundle.packed.in_.valid.W() == 1
    assert bundle.packed.records.indices == (2, 1)
    assert bundle.record.in_.entries.indices == (-1, 0)
    assert bundle.record.in_.entries[-1].header.tag.W() == 5
    assert bundle.record.in_.entries[-1].payload[3].W() == 65

    paths = [path for path, _ in bundle.leaves()]
    assert "bundle.matrix" in paths
    assert "bundle.packed.in_.tag" in paths
    assert "bundle.packed.in_.valid" in paths
    assert "bundle.packed.records[2].tag" in paths
    assert "bundle.packed.records[1].tag" in paths
    assert "bundle.record.in_.entries[-1].header.tag" in paths
    assert "bundle.record.in_.entries[-1].payload[3]" in paths
    assert "bundle.record.in_.entries[-1].payload[2]" in paths
    assert "bundle.record.in_.entries[-1].flag" in paths


def test_real_dut_packed_struct_integer_round_trip(picker_export, picker_dut):
    bundle = Bundle.bind_tree(picker_dut, picker_export.metadata["ports"])
    view = bundle.packed.in_

    view.drive({"tag": 0b10110, "valid": 1})
    assert picker_dut.packed_in.U() == 0b101101
    assert view.tag.U() == 0b10110
    assert view.valid.U() == 1
    assert view.sample().as_dict() == {"tag": 0b10110, "valid": 1}

    picker_dut.Step(1)
    assert bundle.packed.out.sample().as_dict() == {
        "tag": 0b10110,
        "valid": 1,
    }
    assert picker_dut.packed_out.U() == 0b101101

    # Driving the 5-bit field on its own must update only bits [5:1].
    view.tag.Set(0b01010)
    view.valid.Set(0)
    assert picker_dut.packed_in.U() == 0b010100
    picker_dut.Step(1)
    assert bundle.packed.out.tag.U() == 0b01010
    assert bundle.packed.out.valid.U() == 0


def test_real_dut_matrix_integer_round_trip(picker_export, picker_dut):
    bundle = Bundle.bind_tree(picker_dut, picker_export.metadata["ports"])
    view = bundle.matrix

    assert view.W() == 8
    view.drive(0b10010110)
    assert picker_dut.matrix.U() == 0b10010110
    assert view.sample().as_int() == 0b10010110
    picker_dut.Step(1)
    assert picker_dut.matrix_out.U() == 0b10010110


def test_real_dut_packed_records_rtl_array_indices(
    picker_export, picker_dut
):
    bundle = Bundle.bind_tree(picker_dut, picker_export.metadata["ports"])
    records = bundle.packed.records

    assert records.indices == (2, 1)
    assert list(records) == [records[2], records[1]]
    with pytest.raises(IndexError):
        _ = records[0]

    records[2].drive({"tag": 0b00101, "valid": 1})
    records[1].drive({"tag": 0b11010, "valid": 0})
    assert [path for path, _ in records.leaves()] == [
        "bundle.packed.records[2].tag",
        "bundle.packed.records[2].valid",
        "bundle.packed.records[1].tag",
        "bundle.packed.records[1].valid",
    ]
    picker_dut.Step(1)
    assert bundle.packed.records_out[2].sample().as_dict() == {
        "tag": 0b00101,
        "valid": 1,
    }
    assert bundle.packed.records_out[1].sample().as_dict() == {
        "tag": 0b11010,
        "valid": 0,
    }


def test_real_dut_packed_struct_array_fields_and_msb_order(
    picker_struct_array_export, picker_struct_array_dut
):
    assert (
        picker_struct_array_export.metadata["ports"]
        == PACKED_STRUCT_ARRAY_METADATA["ports"]
    )
    bundle = Bundle.bind_tree(
        picker_struct_array_dut, picker_struct_array_export.metadata["ports"]
    )
    view = bundle.arr

    assert view.indices == (2, 1, 0)
    assert [path for path, _ in view.leaves()] == [
        "bundle.arr[2].a",
        "bundle.arr[2].b",
        "bundle.arr[1].a",
        "bundle.arr[1].b",
        "bundle.arr[0].a",
        "bundle.arr[0].b",
    ]
    view.drive(
        {
            2: {"a": 0xA, "b": 1},
            1: {"a": 0x5, "b": 0},
            0: {"a": 0xF, "b": 1},
        }
    )
    assert picker_struct_array_dut.arr.U() == (
        (0b10101 << 10) | (0b01010 << 5) | 0b11111
    )
    picker_struct_array_dut.Step(1)
    assert bundle.arr_out[2].sample().as_dict() == {"a": 0xA, "b": 1}
    assert bundle.arr_out[1].sample().as_dict() == {"a": 0x5, "b": 0}
    assert bundle.arr_out[0].sample().as_dict() == {"a": 0xF, "b": 1}


def test_real_dut_record_in_nested_fields_round_trip(
    picker_export, picker_dut
):
    bundle = Bundle.bind_tree(picker_dut, picker_export.metadata["ports"])
    record_in = bundle.record.in_
    record_out = bundle.record.out

    assert record_in.entries.indices == (-1, 0)
    entry = record_in.entries[-1]
    assert entry.header.tag.W() == 5
    assert entry.payload[3].W() == 65

    record_in.drive(
        {
            "enabled": 1,
            "entries": {
                -1: {
                    "header": {"tag": 0b01101, "valid": 1},
                    "payload": {3: (1 << 64) | 0x55, 2: 0xAA},
                    "flag": 1,
                },
                0: {
                    "header": {"tag": 0b10010, "valid": 0},
                    "payload": {3: 0, 2: 0x1234},
                    "flag": 0,
                },
            },
        }
    )
    assert record_in.enabled.U() == 1
    assert record_in.entries[-1].header.tag.U() == 0b01101
    assert record_in.entries[-1].payload[3].U() == (1 << 64) | 0x55
    picker_dut.Step(1)

    assert record_out.enabled.U() == 1
    assert record_out.entries[-1].header.sample().as_dict() == {
        "tag": 0b01101,
        "valid": 1,
    }
    assert record_out.entries[-1].payload[3].U() == (1 << 64) | 0x55
    assert record_out.entries[-1].payload[2].U() == 0xAA
    assert record_out.entries[-1].flag.U() == 1
    assert record_out.entries[0].header.sample().as_dict() == {
        "tag": 0b10010,
        "valid": 0,
    }
    assert record_out.entries[0].payload[3].U() == 0
    assert record_out.entries[0].payload[2].U() == 0x1234
    assert record_out.entries[0].flag.U() == 0
