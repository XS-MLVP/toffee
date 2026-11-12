"""Bounded unpacked arrays preserve signal identity, indices, and snapshots."""

from collections.abc import Sequence
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from toffee import ArrayValue, Bundle, Field, LogicValue, SignalArray, structural_differences, xtrigger
from toffee.ir import SignalExpr, program_from_dict, program_signal_paths, program_to_dict


class Signal:
    def __init__(self, value=0, *, width=65, mask=0):
        self.value, self.width, self.mask = value, width, mask
        self.writes = 0

    def U(self):
        return self.value

    def W(self):
        return self.width

    def XMask(self):
        return self.mask

    def Set(self, value):
        self.value, self.mask = value, 0
        self.writes += 1


class BoundedView:
    """The public Picker view protocol, without depending on a private class."""

    def __init__(self, bounds, items):
        self.bounds, self.items = tuple(bounds), tuple(items)
        self.lookups = 0

    @property
    def shape(self):
        return tuple(abs(left - right) + 1 for left, right in self.bounds)

    @property
    def indices(self):
        left, right = self.bounds[0]
        step = 1 if left <= right else -1
        return range(left, right + step, step)

    def __getitem__(self, index):
        self.lookups += 1
        return self.items[self.indices.index(index)]


def matrix():
    return BoundedView(((3, 2), (-1, 1)), tuple(
        BoundedView(((-1, 1),), tuple(Signal(i * 10 + j) for j in (-1, 0, 1)))
        for i in (3, 2)
    ))


def test_array_adaptation_is_lazy_and_does_not_copy_leaves():
    source = matrix()
    bundle = Bundle(matrix=source)
    array = bundle.matrix
    assert isinstance(array, SignalArray)
    assert not isinstance(array, Sequence)
    assert source.lookups == 0
    assert array.bounds == ((3, 2), (-1, 1)) and array.shape == (2, 3)
    assert list(array.indices) == [3, 2]
    assert array[3].bounds == ((-1, 1),)
    assert array[3][-1] is source.items[0].items[0]
    assert array[3] is array[3] and array[3][-1] is array[3][-1]
    assert source.lookups == 1 and source.items[0].lookups == 1
    assert [path for path, _ in bundle.leaves()] == [
        f"bundle.matrix[{i}][{j}]" for i in (3, 2) for j in (-1, 0, 1)
    ]
    with pytest.raises(AttributeError):
        array.bounds = ((0, 1),)
    with pytest.raises(TypeError):
        array[3] = array[2]


@pytest.mark.parametrize("index", [4, 1, -1])
def test_array_checks_real_bounds(index):
    with pytest.raises(IndexError, match=r"\[3:2\]"):
        SignalArray(matrix())[index]


@pytest.mark.parametrize("index", [True, 1.0, "3", slice(None)])
def test_array_rejects_non_integer_indices(index):
    with pytest.raises(TypeError, match="integers"):
        SignalArray(matrix())[index]


def test_snapshot_and_compiled_sampler_retain_bounds_and_fresh_values():
    array = SignalArray(matrix())
    array[3][-1].mask = 4
    bundle = Bundle(matrix=array)
    snapshot = bundle.sample()
    assert isinstance(snapshot.matrix, ArrayValue)
    assert snapshot.matrix.bounds == array.bounds
    assert snapshot.matrix[3][-1] == LogicValue(29, 4, 65)
    sample = bundle.compile_sampler()
    assert sample() == snapshot
    array[3][-1].value = 999
    assert snapshot.matrix[3][-1].value == 29
    assert sample().matrix[3][-1].value == 999
    assert snapshot.matrix.as_tuple()[0][0] == LogicValue(29, 4, 65)
    with pytest.raises(FrozenInstanceError):
        snapshot.matrix.bounds = ((0, 1),)
    with pytest.raises(TypeError):
        snapshot.matrix[3] = snapshot.matrix[2]
    with pytest.raises(ValueError, match="X/Z"):
        snapshot.as_dict()


def test_array_drives_snapshots_index_mappings_and_declaration_order_sequences():
    array = SignalArray(matrix())
    bundle = Bundle(matrix=array)
    before = bundle.sample()
    plain = before.as_dict()
    assert plain == {"matrix": {3: {-1: 29, 0: 30, 1: 31}, 2: {-1: 19, 0: 20, 1: 21}}}
    bundle.drive({"matrix": [[1, 2, 3], [4, 5, 6]]})
    assert array[3][-1].U() == 1 and array[2][1].U() == 6
    bundle.drive(before)
    assert bundle.sample() == before
    array[3][-1].Set(100)
    bundle.drive(plain)
    assert bundle.sample() == before
    # Mapping insertion order is irrelevant; array declaration order is fixed.
    array.drive({2: {1: 6, 0: 5, -1: 4}, 3: {1: 3, 0: 2, -1: 1}})
    assert array.sample().as_dict() == {3: {-1: 1, 0: 2, 1: 3}, 2: {-1: 4, 0: 5, 1: 6}}


@pytest.mark.parametrize("bad", [
    [[1, 2, 3], [4, 5]],
    {3: {-1: 1, 0: 2, 1: 3}},
    {3: {-1: 1, 0: 2, 1: 3}, 2: {-1: 4, 0: 5, 2: 6}},
    {"3": [1, 2, 3], "2": [4, 5, 6]},
    [[1, 2, 3], [4, 5, LogicValue(6, 1, 65)]],
    [[1, 2, 3], [4, 5, [6]]],
])
def test_invalid_array_drive_does_not_write_a_partial_array(bad):
    array = SignalArray(matrix())
    before = array.sample()
    with pytest.raises((TypeError, ValueError)):
        array.drive(bad)
    assert array.sample() == before
    assert all(signal.writes == 0 for _, signal in array.leaves())


def test_equal_shape_with_different_bounds_is_not_interchangeable():
    array = SignalArray(matrix())
    snapshot = array.sample()
    changed = ArrayValue(((2, 3), (-1, 1)), tuple(snapshot))
    with pytest.raises(ValueError, match="bounds mismatch"):
        array.drive(changed)
    differences = structural_differences(snapshot, changed)
    assert len(differences) == 1 and differences[0].path == "bounds"


def test_scoreboard_reports_actual_indices_not_snapshot_implementation_fields():
    array = SignalArray(matrix())
    bundle = Bundle(matrix=array)
    before = bundle.sample()
    expected = {"matrix": array.sample()}
    array[3][-1].Set(999)
    differences = structural_differences(expected, {"matrix": array.sample()})
    assert differences[0].path == "matrix[3][-1].value"
    assert all("_items" not in item.path for item in differences)
    assert structural_differences(before, bundle.sample())[0].path == "matrix[3][-1].value"
    assert structural_differences(expected, expected) == ()
    assert structural_differences(array.sample(), (), limit=1)[0].path == "value"


def test_tree_binding_understands_unpacked_and_checks_each_element_width():
    source = matrix()
    dut = SimpleNamespace(io_matrix=source)
    tree = {"matrix": {"_": True, "Pin": "input", "High": 64, "Low": 0,
                       "Unpacked": [[3, 2], [-1, 1]]}}
    bound = Bundle.bind_tree(dut, tree, source_prefix="io")
    assert bound.matrix[3][-1] is source.items[0].items[0]
    assert bound.matrix.bounds == source.bounds
    source.items[1].items[2].width = 64
    with pytest.raises(ValueError, match=r"matrix\[2\]\[1\] width mismatch"):
        Bundle.bind_tree(dut, tree, source_prefix="io")
    tree["matrix"]["Unpacked"] = [[2, 3], [-1, 1]]
    with pytest.raises(ValueError, match="bounds mismatch"):
        Bundle.bind_tree(dut, tree, source_prefix="io")
    with pytest.raises(ValueError, match="requires an array port leaf"):
        Bundle.bind_tree(dut, {"Unpacked": [[0, 1]]})


def test_explicit_field_paths_and_symbolic_indices_resolve_the_same_leaf():
    source = matrix()
    dut = SimpleNamespace(matrix=source)
    field = Bundle.bind(dut, {"word": Field(("matrix", 3, -1), width=65)})
    assert field.word is source.items[0].items[0]

    @xtrigger()
    def selected(pins):
        return pins.matrix[3][-1] == 29

    assert selected(dut).program.evaluate(dut)
    portable = program_to_dict(selected.program)
    assert program_from_dict(portable).evaluate(dut)
    assert program_signal_paths(selected.program) == (("matrix", 3, -1),)
    mixed = SignalExpr(("ports", "ready")) | SignalExpr(("ports", 0, "ready"))
    assert program_signal_paths(mixed) == (("ports", "ready"), ("ports", 0, "ready"))


@pytest.mark.parametrize("index", [True, 1.0, "3", slice(None), SignalExpr(("index",))])
def test_symbolic_indexing_rejects_dynamic_indices_and_slices(index):
    with pytest.raises(TypeError, match="constant integer"):
        SignalExpr(("matrix",))[index]


@pytest.mark.parametrize("bounds,items,error", [
    ((), (), ValueError),
    (((0,),), (), ValueError),
    (((False, 0),), (), TypeError),
    (("01",), (), TypeError),
    (((0, 1),), (LogicValue(0, 0, 1),), ValueError),
    (((0, 0),), (1,), TypeError),
    (((0, 0), (0, 0)), (LogicValue(0, 0, 1),), ValueError),
])
def test_malformed_snapshots_are_rejected(bounds, items, error):
    with pytest.raises(error):
        ArrayValue(bounds, items)


def test_array_sources_require_consistent_explicit_metadata():
    with pytest.raises(TypeError, match="bounded signal view"):
        SignalArray([Signal()])
    source = SimpleNamespace(bounds=((0, 1),), shape=(3,), indices=range(2))
    with pytest.raises(TypeError):
        SignalArray(source)
    bad = BoundedView(((0, 1), (0, 1)), (BoundedView(((1, 2),), (Signal(), Signal())),) * 2)
    with pytest.raises(ValueError, match="parent"):
        SignalArray(bad)[0]


def test_array_shape_and_readonly_errors_do_not_touch_other_elements():
    class BadShape(BoundedView):
        @property
        def shape(self):
            return (99,)

    with pytest.raises(ValueError, match="shape"):
        SignalArray(BadShape(((0, 1),), (Signal(), Signal())))
    for flag, message in (("IsOutIO", "DUT output"), ("IsReadonly", "read-only")):
        array = SignalArray(matrix())
        setattr(array[2][1], flag, lambda: True)
        with pytest.raises(ValueError, match=message):
            array.drive([[1, 2, 3], [4, 5, 6]])
        assert all(signal.writes == 0 for _, signal in array.leaves())


@pytest.mark.parametrize("path", [[], ["matrix", True], ["matrix", 1.0], [""]])
def test_portable_paths_reject_ambiguous_or_invalid_index_tokens(path):
    with pytest.raises((TypeError, ValueError)):
        program_from_dict({"type": "SignalExpr", "path": path})


def test_existing_attribute_only_portable_paths_remain_compatible():
    restored = program_from_dict({"type": "SignalExpr", "path": ["matrix"]})
    assert restored.path == ("matrix",)


def test_array_snapshots_cannot_be_mistaken_for_live_signal_views():
    snapshot = SignalArray(matrix()).sample()
    with pytest.raises(TypeError, match="not live"):
        SignalArray(snapshot)
    with pytest.raises(TypeError, match="not a live"):
        Bundle(matrix=snapshot)


def test_static_array_paths_do_not_add_implicit_packed_bit_selection():
    class BitSelectableSignal(Signal):
        def __getitem__(self, index):
            return (self.value >> index) & 1

    dut = SimpleNamespace(word=BitSelectableSignal(3))
    with pytest.raises(TypeError, match="not packed signal bits"):
        SignalExpr(("word", 0)).evaluate(dut)
