"""Public data views reject malformed layouts and preserve live/snapshot semantics."""

from types import SimpleNamespace

import pytest

from toffee import Bundle, BundleValue, Field, LogicValue, PackedArray, PackedLayout, PackedView, split_packed
from toffee.signals import read_bool, sample_signal, signal_width, write_signal


def test_packed_layout_extensions_require_a_width_implementation():
    class UndefinedLayout(PackedLayout):
        offset = None

    with pytest.raises(NotImplementedError):
        PackedLayout.array(1, UndefinedLayout())


def test_native_view_rejects_an_unsupported_layout_extension():
    xspcomm = pytest.importorskip("xspcomm")

    class UnsupportedLayout(PackedLayout):
        offset = None

        @property
        def width(self):
            return 4

    signal = xspcomm.XData(4, xspcomm.XData.InOut)
    with pytest.raises(TypeError, match="unsupported packed layout"):
        PackedView(signal, UnsupportedLayout())


@pytest.mark.parametrize("factory,message", [
    (lambda: Field("data", width=0), "positive"),
    (lambda: Bundle(), "at least one"),
    (lambda: Bundle({"a": SimpleNamespace(value=0)}, b=SimpleNamespace(value=0)), "either"),
    (lambda: Bundle(a=[]), "empty"),
    (lambda: Bundle(a=object()), "signal"),
    (lambda: PackedLayout.bits(0), "positive"),
    (lambda: PackedLayout.bits(1, offset=-1), "negative"),
    (lambda: PackedLayout.array(2, object()), "PackedLayout"),
    (lambda: PackedLayout.array(2, PackedLayout.bits(1, offset=0)), "offset"),
    (lambda: PackedLayout.array(0, PackedLayout.bits(1)), "positive"),
    (lambda: PackedLayout.array(2, PackedLayout.bits(8), stride=4), "stride"),
    (lambda: PackedLayout.struct({}), "at least one"),
    (lambda: PackedLayout.struct({"sample": PackedLayout.bits(1)}), "name"),
    (lambda: PackedLayout.struct({"x": object()}), "PackedLayout"),
    (lambda: PackedLayout.struct({"x": PackedLayout.bits(1)}, width=0), "positive"),
    (lambda: PackedLayout.struct({"x": PackedLayout.bits(8)}, width=4), "too small"),
    (lambda: PackedArray(SimpleNamespace(width=8), 0), "positive"),
    (lambda: PackedArray(SimpleNamespace(width=9), 4), "divisible"),
    (lambda: PackedArray(SimpleNamespace(width=8), 8, count=0), "positive"),
    (lambda: PackedArray(SimpleNamespace(width=8), 4, count=1), "shape"),
    (lambda: PackedArray(SimpleNamespace(width=8), 4), "SubDataRef"),
    (lambda: PackedView(SimpleNamespace(width=8), object()), "PackedLayout"),
    (lambda: PackedView(SimpleNamespace(width=8), PackedLayout.bits(8)), "root"),
    (lambda: PackedView(SimpleNamespace(width=8), PackedLayout.array(2, PackedLayout.bits(4))), "SubDataRef"),
    (lambda: BundleValue((("x", 1), ("x", 2))), "unique"),
])
def test_invalid_data_shapes_are_rejected(factory, message):
    with pytest.raises((TypeError, ValueError), match=message):
        factory()


def test_nested_snapshots_round_trip_without_aliasing_live_signals():
    root = SimpleNamespace(lanes=[SimpleNamespace(value=3, width=4), SimpleNamespace(value=5, width=4)])
    bundle = Bundle.bind(root, {"header": {"a": Field(("lanes", 0), width=4)},
                               "lanes": [Field(("lanes", 0)), root.lanes[1]]})
    assert len(bundle) == 2 and tuple(bundle.fields) == ("header", "lanes")
    snapshot = bundle.sample()
    assert len(snapshot) == 2 and tuple(snapshot) == ("header", "lanes")
    assert snapshot.as_dict() == {"header": {"a": 3}, "lanes": (3, 5)}
    bundle.drive(snapshot)
    root.lanes[0].value = 9
    assert snapshot.header.a.as_int() == 3
    for value in (bundle, snapshot):
        with pytest.raises(AttributeError, match="missing"):
            value.missing
    with pytest.raises(KeyError, match="missing"):
        snapshot["missing"]
    with pytest.raises(TypeError, match="mapping"):
        bundle.drive([1, 2])
    with pytest.raises(ValueError, match="2 values"):
        bundle.drive({"header": {"a": 3}, "lanes": [1]})


@pytest.mark.parametrize("tree,prefix,message", [
    ([], "io", "mapping"),
    ({"_": True, "child": {}}, "io", "both"),
    ({"_": True}, "", "source path"),
    ({}, "io", "no fields"),
    ({"0": {"_": True}, "2": {"_": True}}, "io", "contiguous"),
    ({"not-valid": {"_": True}}, "io", "identifier"),
    ({"in": {"_": True}, "in_": {"_": True}}, "io", "duplicate"),
    ({"_": True}, "io", "aggregate"),
])
def test_malformed_signal_tree_has_actionable_diagnostics(tree, prefix, message):
    root = SimpleNamespace(io=SimpleNamespace(value=0), io_in=SimpleNamespace(value=0))
    with pytest.raises((TypeError, ValueError), match=message):
        Bundle.bind_tree(root, tree, source_prefix=prefix)


def test_signal_compatibility_handles_values_widths_and_unknown_drive():
    known = SimpleNamespace(value=LogicValue(3, 0, 4))
    assert read_bool(known) and signal_width(known) == 4
    assert sample_signal(known) is known.value
    assert signal_width(SimpleNamespace(width=3, value=0)) == 3
    assert signal_width(SimpleNamespace(W=lambda: 0)) == 1
    assert signal_width(SimpleNamespace(value=8)) == 4
    assert int(known.value) == 3
    target = SimpleNamespace(value=0)
    write_signal(target, known.value)
    assert target.value == 3
    with pytest.raises(ValueError, match="X/Z"):
        write_signal(target, LogicValue(3, 1, 4), "payload")
    assert target.value == 3
    with pytest.raises(TypeError, match="integer-compatible"):
        read_bool(SimpleNamespace(value=object()), "ready")
    with pytest.raises(ValueError, match="positive"):
        signal_width(SimpleNamespace(width=0))
    with pytest.raises(TypeError, match="determine"):
        signal_width(object())
    with pytest.raises(TypeError, match="Set"):
        write_signal(object(), 1)


def test_native_packed_views_keep_parent_identity_and_validate_drives():
    native = pytest.importorskip("xspcomm")
    bus = native.XData(16, native.XData.InOut)
    layout = PackedLayout.struct({"tag": PackedLayout.bits(8), "data": PackedLayout.bits(8)})
    view = PackedView(bus, layout)
    assert view.parent is bus and view.layout is layout
    assert len(view) == 2 and tuple(view.fields) == ("tag", "data")
    view.drive({"tag": 1, "data": 2})
    assert bus.U() == 0x0201
    assert view.sample().as_dict() == {"tag": 1, "data": 2}
    with pytest.raises(AttributeError, match="missing"):
        view.missing
    with pytest.raises(TypeError, match="mapping"):
        view.drive([1, 2])
    with pytest.raises(ValueError, match="shape"):
        view.drive({"tag": 3})
    assert bus.U() == 0x0201
    array = PackedView(bus, PackedLayout.array(2, PackedLayout.bits(8)))
    with pytest.raises(AttributeError, match="fields"):
        array.fields
    with pytest.raises(AttributeError, match="missing"):
        array.missing
    with pytest.raises(ValueError, match="2 values"):
        array.drive([1])
    lanes = split_packed(bus, 8)
    assert isinstance(lanes, PackedArray) and len(tuple(lanes)) == 2
    wrapped = Bundle(array=array, lanes=lanes)
    assert [path for path, _ in wrapped.leaves()] == ["bundle.array[0]", "bundle.array[1]", "bundle.lanes[0]", "bundle.lanes[1]"]
    wrapped.drive({"array": [3, 4], "lanes": [5, 6]})
    assert bus.U() == 0x0605
    for strict in (True, False):
        with pytest.raises(ValueError, match="bits"):
            PackedView(bus, PackedLayout.array(3, PackedLayout.bits(8)), strict_width=strict)
    with pytest.raises(ValueError, match="bits"):
        PackedView(bus, PackedLayout.array(1, PackedLayout.bits(8)))
