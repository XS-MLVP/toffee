"""Bind real Picker port metadata and project packed layouts.

This first stage covers Layout/Unpacked parsing and packed projection.  The
fixtures are verbatim ``picker config --section metadata`` output; see
``fixtures/README.md`` for the Picker version and commands.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from toffee import (
    Bundle,
    LogicValue,
    PackedLayout,
    PackedView,
    PortLayout,
    PortView,
)

FIXTURES = Path(__file__).with_name("fixtures")


def _load_metadata(name: str) -> dict:
    with (FIXTURES / name).open(encoding="utf-8") as handle:
        return json.load(handle)


AGGREGATE_METADATA = _load_metadata(
    "picker_aggregate_views_metadata.json"
)


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


class Dut:
    pass


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

