"""Recursive source protocols work without importing xcomm or copying signals."""

from types import SimpleNamespace

import pytest

from toffee import ArrayValue, Bundle, BundleValue, Field, LogicValue, ReadyValid, SignalArray, structural_differences, xtrigger
from toffee.data import compile_sampler, drive_data, iter_data_leaves, normalize_data, sample_data


class Signal:
    def __init__(self, value=0, *, width=65, mask=0):
        self.value, self.width, self.mask = value, width, mask
        self.reads = self.writes = 0

    def U(self):
        self.reads += 1
        return self.value

    def W(self):
        return self.width

    def XMask(self):
        return self.mask

    def Set(self, value):
        self.value, self.mask = value, 0
        self.writes += 1


class Struct:
    def __init__(self, **fields):
        self.members = fields

    def Names(self):
        return tuple(self.members)

    def Field(self, name):
        return self.members[name]

    def IsPacked(self):
        return False

    def __getitem__(self, name):
        return self.members[name]


class Array:
    """XArray has scalar Left/Right as well as the bounded Python protocol."""
    bounds = ((-1, 0),)
    shape = (2,)
    indices = range(-1, 1)

    def __init__(self, *items):
        self.items = items
        self.lookups = 0

    def Left(self):
        return -1

    def Right(self):
        return 0

    def Shape(self):
        return self.shape

    def At(self, index):
        return self[index]

    def __getitem__(self, index):
        self.lookups += 1
        return self.items[self.indices.index(index)]


def source():
    return Struct(enabled=Signal(width=1), entries=Array(
        Struct(header=Struct(tag=Signal(3, width=5)), word=Signal(1 << 64)),
        Struct(header=Struct(tag=Signal(4, width=5)), word=Signal(7)),
    ))


def values():
    return {"enabled": 1, "entries": {
        -1: {"header": {"tag": 5}, "word": (1 << 64) | 9},
        0: {"header": {"tag": 6}, "word": 10},
    }}


def test_struct_normalization_is_live_and_array_elements_stay_lazy():
    raw = source()
    bound = normalize_data(raw)
    assert isinstance(bound, Bundle) and isinstance(bound.entries, SignalArray)
    assert raw["entries"].lookups == 0
    assert bound.enabled is raw["enabled"]
    assert bound.entries[-1].header.tag is raw["entries"].items[0]["header"]["tag"]
    assert bound.entries[-1] is bound.entries[-1]
    assert raw["entries"].lookups == 1
    assert all(leaf.reads == 0 for _, leaf in iter_data_leaves(bound))
    assert [path for path, _ in bound.leaves()] == [
        "data.enabled", "data.entries[-1].header.tag", "data.entries[-1].word",
        "data.entries[0].header.tag", "data.entries[0].word",
    ]
    with pytest.raises(TypeError):
        bound.entries[-1] = bound.entries[0]


def test_snapshots_and_compiled_sampling_preserve_recursive_shapes_and_unknowns():
    raw = source()
    bound = Bundle(raw)
    bound.entries[0].word.mask = 1 << 64
    sample = compile_sampler(raw)
    before = sample_data(raw)
    assert sample() == before
    assert isinstance(before.entries, ArrayValue)
    assert isinstance(before.entries[-1], BundleValue)
    assert before.entries.bounds == ((-1, 0),)
    assert before.entries[0].word == LogicValue(7, 1 << 64, 65)
    with pytest.raises(ValueError, match="X/Z"):
        before.as_dict()
    bound.entries[0].word.Set(99)
    assert sample().entries[0].word.as_int() == 99
    assert before.entries[0].word.value == 7
    assert structural_differences(before, sample())[0].path == "entries[0].word.value"


def test_recursive_drive_accepts_index_maps_sequences_and_immutable_snapshots():
    raw = source()
    drive_data(raw, values())
    saved = sample_data(raw)
    assert saved.as_dict() == values()
    assert sample_data(raw).entries.as_dict() == values()["entries"]
    assert sample_data(raw).entries.as_tuple()[0] == saved.entries[-1]
    bound = Bundle(raw)
    bound.drive({"enabled": 0, "entries": [
        {"header": {"tag": 1}, "word": 2}, {"header": {"tag": 3}, "word": 4},
    ]})
    bound.drive(saved)
    assert bound.sample() == saved


@pytest.mark.parametrize("failure", ["missing", "extra", "unknown", "indices", "output", "readonly"])
def test_recursive_drive_validates_every_leaf_before_any_write(failure):
    bound = Bundle(source())
    transaction = values()
    last = transaction["entries"][0]
    if failure == "missing":
        del last["word"]
    elif failure == "extra":
        last["extra"] = 1
    elif failure == "unknown":
        last["word"] = LogicValue(1, 1, 65)
    elif failure == "indices":
        transaction["entries"][1] = transaction["entries"].pop(0)
    else:
        flag = "IsOutIO" if failure == "output" else "IsReadonly"
        setattr(bound.entries[0].word, flag, lambda: True)
    with pytest.raises((TypeError, ValueError)):
        bound.drive(transaction)
    assert all(leaf.writes == 0 for _, leaf in bound.leaves())


def test_native_struct_names_do_not_shadow_bundle_operations():
    raw = Struct(**{name: Signal() for name in ("sample", "fields", "items", "_private", "in")})
    bound = Bundle(raw)
    assert tuple(bound) == raw.Names()
    assert callable(bound.sample) and callable(bound.items)
    assert bound["fields"] is raw["fields"]
    bound.drive({name: index for index, name in enumerate(raw.Names())})
    assert bound.sample().as_dict() == {name: index for index, name in enumerate(raw.Names())}
    with pytest.raises(TypeError):
        bound.fields["sample"] = Signal()


def test_explicit_typed_view_nodes_are_unwrapped_without_reading_values():
    raw = source()

    class TypedView:
        def Kind(self):
            return "struct"

        def Struct(self):
            return raw

        def Data(self):
            raise AssertionError("a struct is not a scalar")

        def Array(self):
            raise AssertionError("a struct is not an array")

    bound = normalize_data(TypedView())
    assert bound.enabled is raw["enabled"]
    assert raw["enabled"].reads == 0


def test_packed_layout_metadata_does_not_implicitly_request_a_view():
    layout = {"kind": "struct", "packed": True, "width": 4,
              "fields": [{"name": "word", "offset": 0,
                          "type": {"kind": "bits", "packed": True, "width": 4}}]}
    tree = {"packet": {"_": True, "High": 3, "Low": 0, "Layout": layout}}
    signal = Signal(width=4)
    bound = Bundle.bind_tree(SimpleNamespace(packet=signal), tree)
    assert bound.packet is signal
    bound.drive({"packet": 7})
    assert bound.sample().packet.as_int() == 7
    assert signal.reads == 1
    with pytest.raises(ValueError, match="metadata requires"):
        Bundle.bind_tree(SimpleNamespace(), {"Layout": layout})


def test_unpacked_layout_validates_fields_bounds_and_widths():
    bits = {"kind": "bits", "packed": True, "width": 65}
    layout = {"kind": "struct", "packed": False, "width": 0,
              "fields": [{"name": "words", "type": {"kind": "array", "packed": False,
                          "left": -1, "right": 0, "element": bits}}]}
    raw = Struct(words=Array(Signal(), Signal()))
    tree = {"packet": {"_": True, "Layout": layout}}
    bound = Bundle.bind_tree(SimpleNamespace(packet=raw), tree)
    assert bound.packet.words[-1] is raw["words"].items[0]
    raw["words"].items[1].width = 64
    with pytest.raises(ValueError, match=r"words\[0\] width mismatch"):
        Bundle.bind_tree(SimpleNamespace(packet=raw), tree)
    raw["words"].items[1].width = 65
    layout["fields"][0]["type"]["left"] = 0
    with pytest.raises(ValueError, match="bounds mismatch"):
        Bundle.bind_tree(SimpleNamespace(packet=raw), tree)


def test_ready_valid_tree_accepts_a_bounded_struct_array_payload():
    raw = source()["entries"]
    root = SimpleNamespace(valid=Signal(width=1), ready=Signal(width=1), bits=raw)
    tree = {"valid": {"_": True}, "ready": {"_": True}, "bits": {"_": True}}
    interface = ReadyValid.bind_tree(root, tree, clock=object(), source_prefix="")
    assert isinstance(interface.bits, SignalArray)
    assert interface.bits[-1].header.tag is raw.items[0]["header"]["tag"]


def test_packed_metadata_width_must_agree_with_layout():
    tree = {"packet": {"_": True, "High": 7, "Layout": {"kind": "bits", "packed": True, "width": 4}}}
    with pytest.raises(ValueError, match="metadata=8, Layout=4"):
        Bundle.bind_tree(SimpleNamespace(packet=Signal(width=4)), tree)


@pytest.mark.parametrize("adapt", [False, True])
def test_static_paths_select_struct_fields_instead_of_adapter_methods(adapt):
    raw = Struct(sample=Signal(7, width=4))
    root = SimpleNamespace(packet=Bundle(raw) if adapt else raw)
    bound = Bundle.bind(root, {"word": Field(("packet", "sample"), width=4)})
    assert bound.word is raw["sample"]

    @xtrigger()
    def selected(pins):
        return pins.packet.sample == 7

    assert selected(root).program.evaluate(root)


def test_associated_signal_types_are_interpreted_only_by_explicit_views():
    class TypedSignal(Signal):
        def HasLayout(self):
            return True

        def StructView(self):
            raise AssertionError("the caller chooses structural interpretation")

        ArrayView = StructView

    signal = TypedSignal(0xa5, width=8)
    assert normalize_data(signal) is signal
    bound = Bundle(packet=signal)
    assert bound.packet is signal
    assert signal.reads == 0
    bound.drive({"packet": 0x72})
    assert bound.sample().packet == LogicValue(0x72, 0, 8)
    with pytest.raises(ValueError, match="scalar drive value"):
        bound.drive({"packet": {"tag": 7, "payload": 2}})
