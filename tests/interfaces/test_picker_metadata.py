"""Useful PR34 regressions, adapted to current packed-Data/view semantics."""

import copy
import json
from types import SimpleNamespace

import pytest

from toffee import Bundle, ReadyValid, Role
from toffee.data import compile_sampler


class Signal:
    def __init__(self, width=8):
        self.width = width
        self.value = self.reads = 0

    def W(self):
        return self.width

    def U(self):
        self.reads += 1
        return self.value

    def Set(self, value):
        self.value = value

    def HasLayout(self):
        return True

    def StructView(self, *args):
        raise AssertionError("metadata must not implicitly request a view")

    ArrayView = StructView


def bits(width=0):
    return {"kind": "bits", "packed": True, "width": width}


def matrix_layout():
    return {"kind": "array", "packed": True, "width": 8, "left": 1, "right": 0,
            "element": {"kind": "array", "packed": True, "width": 4,
                        "left": -2, "right": 1, "element": bits()}}


def leaf(width=1, **extra):
    return {"_": True, "High": width - 1, "Low": 0, **extra}


def test_port_prefix_collisions_preserve_all_original_leaves():
    widths = {"data": 1, "data_0": 4, "data_0_out": 4, "data_out": 1,
              "req": 1, "req_out": 1, "req_valid": 1, "req_valid_out": 1}
    root = SimpleNamespace(**{"io_" + name: Signal(width) for name, width in widths.items()})
    tree = {
        "data": leaf(**{"0": leaf(4, out=leaf(4)), "out": leaf()}),
        "req": leaf(out=leaf(), valid=leaf(out=leaf())),
    }
    bound = Bundle.bind_tree(root, tree, source_prefix="io")
    assert tuple(bound) == tuple(widths)
    assert all(bound[name] is getattr(root, "io_" + name) for name in widths)
    assert all(signal.reads == 0 for _, signal in bound.leaves())
    values = {name: index & ((1 << widths[name]) - 1) for index, name in enumerate(widths)}
    bound.drive(values)
    assert bound.sample().as_dict() == values
    assert compile_sampler(bound)() == bound.sample()


def test_numeric_prefix_collision_is_promoted_without_dropping_children():
    root = SimpleNamespace(data_0=Signal(), data_0_out=Signal(), data_1=Signal())
    tree = {"data": {"0": leaf(8, out=leaf(8)), "1": leaf(8)}}
    bound = Bundle.bind_tree(root, tree)
    assert tuple(bound) == ("data_0", "data_0_out", "data_1")
    assert bound.data_0 is root.data_0 and bound.data_0_out is root.data_0_out


def test_noncolliding_numeric_groups_keep_their_existing_sequence_shape():
    root = SimpleNamespace(data_0=Signal(), data_1=Signal())
    bound = Bundle.bind_tree(root, {"data": {"0": leaf(8), "1": leaf(8)}})
    assert bound.data == (root.data_0, root.data_1)


def test_nested_colliding_numeric_groups_retain_every_leaf():
    names = ("data_0_0", "data_0_0_out", "data_0_1", "data_1_0", "data_1_1")
    root = SimpleNamespace(**{name: Signal() for name in names})
    tree = {"data": {"0": {"0": leaf(8, out=leaf(8)), "1": leaf(8)},
                     "1": {"0": leaf(8), "1": leaf(8)}}}
    bound = Bundle.bind_tree(root, tree)
    assert bound.data_0_0 is root.data_0_0
    assert bound.data_0_0_out is root.data_0_0_out
    assert bound.data_0_1 is root.data_0_1
    assert bound.data_1 == (root.data_1_0, root.data_1_1)


def test_flattened_name_conflicts_are_rejected_instead_of_overwritten():
    root = SimpleNamespace(data=Signal(), data_0=Signal())
    tree = {"data": leaf(8, **{"0": leaf(8)}), "data_0": leaf(8)}
    with pytest.raises(ValueError, match="duplicate field after flattening"):
        Bundle.bind_tree(root, tree)


@pytest.mark.parametrize("encoded", [False, True])
@pytest.mark.parametrize("kind", ["struct", "array"])
def test_packed_metadata_retains_whole_data_and_never_calls_view_factories(encoded, kind):
    layout = matrix_layout() if kind == "array" else {
        "kind": "struct", "packed": True, "width": 8,
        "fields": [{"name": "word", "offset": 0, "type": bits(8)}],
    }
    original = copy.deepcopy(layout)
    signal = Signal()
    tree = {"packet": leaf(8, Layout=json.dumps(layout) if encoded else layout)}
    bound = Bundle.bind_tree(SimpleNamespace(packet=signal), tree)
    assert bound.packet is signal and signal.reads == 0
    assert layout == original
    bound.drive({"packet": 0x96})
    assert bound.sample().packet.as_int() == 0x96


def test_ready_valid_tree_accepts_an_explicitly_typed_packed_data_payload():
    root = SimpleNamespace(valid=Signal(1), ready=Signal(1), bits=Signal())
    tree = {"valid": leaf(), "ready": leaf(), "bits": leaf(8, Layout=matrix_layout())}
    interface = ReadyValid.bind_tree(root, tree, clock=object(), source_prefix="", role=Role.MONITOR)
    assert interface.bits is root.bits


@pytest.mark.parametrize("layout,message", [
    ({"kind": "union", "width": 8}, "must describe"),
    ({"kind": "bits", "width": True}, "nonnegative integer"),
    ({"kind": "bits", "width": -1}, "nonnegative integer"),
    ({"kind": "bits", "width": 8, "packed": 1}, "boolean"),
    ({"kind": "array", "packed": True, "width": 8, "left": True,
      "right": 0, "element": bits(4)}, "bounds"),
    ({"kind": "array", "packed": True, "width": 7, "left": 1,
      "right": 0, "element": bits(4)}, "does not match"),
    ({"kind": "struct", "packed": True, "width": 8, "fields": []}, "nonempty"),
    ({"kind": "struct", "packed": True, "width": 8, "fields": [
        {"name": "x", "offset": 0, "type": bits(4)},
        {"name": "x", "offset": 4, "type": bits(4)}]}, "unique"),
    ({"kind": "struct", "packed": True, "width": 8, "fields": [
        {"name": "x", "offset": 7, "type": bits(4)}]}, "exceeds"),
    ("{not json", "invalid Layout JSON"),
    ("[]", "must describe"),
])
def test_malformed_metadata_has_path_qualified_errors(layout, message):
    with pytest.raises(ValueError, match="bundle.packet.*" + message):
        Bundle.bind_tree(SimpleNamespace(packet=Signal()), {"packet": leaf(8, Layout=layout)})


def test_layout_nesting_is_bounded_without_constructing_a_view_graph():
    layout = bits(8)
    for _ in range(70):
        layout = {"kind": "array", "packed": True, "width": 8,
                  "left": 0, "right": 0, "element": layout}
    with pytest.raises(ValueError, match="nesting exceeds 64"):
        Bundle.bind_tree(SimpleNamespace(packet=Signal()), {"packet": leaf(8, Layout=layout)})


def test_missing_port_has_a_path_qualified_error():
    with pytest.raises(ValueError, match="bundle.packet.*io_packet.*not available"):
        Bundle.bind_tree(SimpleNamespace(), {"packet": leaf(8)}, source_prefix="io")
