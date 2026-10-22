"""Malformed definitions fail early; structured samples remain atomic and portable."""

from enum import Enum
from types import MappingProxyType, SimpleNamespace

import pytest

from toffee import Bin, CoverGroupDef, CoverPointDef, CrossDef, CoverageDatabase, CoverageSchemaError, Iff, LogicValue
from toffee.coverage import BinKind, BinSpec, DefaultMatcher, IllegalBinError, ValueMatcher


def point(name="value", **kwargs):
    return CoverPointDef(name, {"zero": Bin.values(0)}, **kwargs)


@pytest.mark.parametrize("factory,message", [
    (lambda: Bin.values(), "at least one"),
    (lambda: Bin.values(1, 1), "duplicate"),
    (lambda: Bin.values(object()), "bool/int/str"),
    (lambda: Bin.range(True, 2), "not bool"),
    (lambda: Bin.range(0, "x"), "integers"),
    (lambda: Bin.range(2, 1), "backwards"),
    (lambda: Bin.ranges(), "at least one"),
    (lambda: Bin.masked(0, 0, width=0), "positive"),
    (lambda: Bin.masked(-1, 1), "non-negative"),
    (lambda: Bin.masked(4, 4, width=2), "fit width"),
    (lambda: Bin.transition(1), "two samples"),
    (lambda: Bin.transition(1, 2, overlap=1), "bool"),
    (lambda: Bin.values(1, at_least=0), "positive"),
    (lambda: BinSpec(DefaultMatcher()), "default bin kind"),
    (lambda: BinSpec(ValueMatcher((1,)), BinKind.DEFAULT), "default matcher"),
    (lambda: Bin.ignore(Bin.default()), "ignored"),
    (lambda: Bin.illegal(Bin.default()), "illegal"),
    (lambda: Bin.array("", 0, 1), "prefix"),
    (lambda: Bin.array("x", 1, 0), "backwards"),
    (lambda: Bin.array("x", 0, 1, count=0), "positive"),
    (lambda: Iff(""), "source"),
    (lambda: Iff("ready", ()), "accepted"),
    (lambda: point(""), "name"),
    (lambda: point(description=3), "description"),
    (lambda: point(weight=-1), "weight"),
    (lambda: point(goal=101), "between"),
    (lambda: CoverPointDef("x", {}), "no bins"),
    (lambda: CoverPointDef("x", {"": Bin.values(0)}), "empty"),
    (lambda: CoverPointDef("x", {"a": Bin.default(), "b": Bin.default(), "c": Bin.values(0)}), "multiple"),
    (lambda: CoverPointDef("x", {"a": Bin.default()}), "normal bin"),
    (lambda: CrossDef("", ("a", "b")), "name"),
    (lambda: CrossDef("x", ("a", "b"), description=3), "description"),
    (lambda: CrossDef("x", ("a", "a")), "distinct"),
    (lambda: CrossDef("x", ("a", "b"), at_least=0), "at_least"),
    (lambda: CrossDef("x", ("a", "b"), weight=-1), "weight"),
    (lambda: CrossDef("x", ("a", "b"), max_auto_bins=0), "max_auto_bins"),
    (lambda: CoverGroupDef("", (point(),)), "name"),
    (lambda: CoverGroupDef("g", (point(),), description=3), "description"),
    (lambda: CoverGroupDef("g", ()), "at least one"),
    (lambda: CoverGroupDef("g", (point(), point())), "duplicate"),
    (lambda: CoverGroupDef("g", (point(weight=0),)), "positive-weight"),
    (lambda: CoverGroupDef("g", (point("a"), point("b")), (CrossDef("a", ("a", "b")),)), "distinct"),
    (lambda: CoverGroupDef("g", (point("a"), point("b")), (CrossDef("x", ("a", "missing")),)), "unknown point"),
    (lambda: CoverGroupDef("g", (point("a"), point("b")), (CrossDef("x", ("a", "b"), include=(("zero", "zero"),) * 2),)), "duplicate include"),
    (lambda: CoverGroupDef("g", (point("a"), point("b")), (CrossDef("x", ("a", "b"), include=(("missing", "zero"),)),)), "invalid include"),
    (lambda: CoverGroupDef("g", (point("a"), point("b")), (CrossDef("x", ("a", "b"), ignore=(("zero", "zero"),)),)), "no eligible"),
    (lambda: IllegalBinError([]), "at least one"),
    (lambda: CoverGroupDef.from_dict({"version": 999}), "version"),
    (lambda: BinSpec.from_dict({"matcher": {"type": "invalid"}, "kind": "normal", "at_least": 1}), "matcher"),
])
def test_malformed_coverage_definitions_are_rejected(factory, message):
    with pytest.raises(ValueError, match=message):
        factory()


def test_enum_bins_nested_samples_and_metadata_survive_serialization():
    class State(Enum):
        IDLE = 0
        BUSY = 1

    class Note:
        def __repr__(self):
            return "capture-note"

    definition = CoverGroupDef("state", (
        CoverPointDef("state", {"idle": Bin.values(State.IDLE), "busy": Bin.ranges((1, 2))},
                      source="header.state", iff=Iff.equals("ready", State.BUSY)),
        CoverPointDef("mask", {"set": Bin.masked(1, 1)}, source="header.mask"),
    ))
    group = definition.instantiate("dut", run_metadata={"state": State.IDLE, "note": Note(), "tags": {1, 2}})
    group.sample(SimpleNamespace(ready=State.BUSY, header=SimpleNamespace(state=State.IDLE, mask=State.BUSY)))
    group.sample({"ready": LogicValue(1, 0, 1), "header": {"state": State.BUSY, "mask": 1}})
    assert group.point_coverage("state") == 100
    before = group.report()
    for sample in ({"ready": 1}, SimpleNamespace(ready=1), {"ready": 1, "header": {}},
                   {"ready": 1, "header": MappingProxyType({})}):
        with pytest.raises((KeyError, AttributeError), match="header"):
            group.sample(sample)
        assert group.report() == before
    restored = type(group).from_report(before)
    assert restored.report() == before
    metadata = next(iter(before["origins"].values()))["metadata"]
    assert metadata["state"] == 0 and metadata["note"] == "capture-note"
    assert set(metadata["tags"]) == {1, 2}
    assert not Iff.equals("ready", 1).enabled({"ready": LogicValue(1, 1, 1)})


def test_ranges_and_masks_do_not_treat_booleans_as_integer_observations():
    definition = CoverGroupDef("numeric", (
        CoverPointDef("value", {"range": Bin.ranges((0, 2), (4, 5)), "mask": Bin.masked(0, 1),
                                "other": Bin.default()}, overlap="allow"),
    ))
    group = definition.instantiate("dut")
    group.sample({"value": True})
    counts = group.report()["points"][0]["counts"]
    assert counts == {"range": 0, "mask": 0, "other": 1}
    group.sample({"value": 4})
    assert group.report()["points"][0]["counts"] == {"range": 1, "mask": 1, "other": 1}


def test_empty_and_weighted_database_and_named_coverage_queries():
    assert CoverageDatabase().coverage == 0
    definition = CoverGroupDef("g", (point("a"), point("b")), (CrossDef("ab", ("a", "b")),))
    group = definition.instantiate("dut")
    group.sample({"a": 0, "b": 0})
    assert group.cross_coverage("ab") == 100
    for method in (group.point_coverage, group.cross_coverage):
        with pytest.raises(KeyError, match="missing"):
            method("missing")
    db = CoverageDatabase([group])
    assert len(db.groups) == 1 and next(iter(db.groups.values())) is group
    with pytest.raises(ValueError, match="instance"):
        definition.instantiate("")
    with pytest.raises(ValueError, match="run_id"):
        definition.instantiate("dut", run_id="")


def test_matchers_accept_enum_observations_and_default_is_selected_by_group():
    class Code(Enum):
        ZERO = 0

    for bin_ in (Bin.values(0), Bin.range(0, 1), Bin.masked(0, 1)):
        assert bin_.matcher.matches(Code.ZERO)
    assert not Bin.default().matcher.matches(Code.ZERO)


def test_overlap_error_rejects_ambiguous_normal_bins():
    with pytest.raises(CoverageSchemaError, match="overlapping normal bins"):
        CoverPointDef("value", {"one": Bin.values(1), "range": Bin.range(0, 2)}, overlap="error")


@pytest.mark.parametrize("details", [False, True])
def test_known_logic_values_and_mapping_proxy_samples_are_counted(details):
    group = CoverGroupDef("logic", (point(),)).instantiate("dut")
    group.sample(MappingProxyType({"value": LogicValue(0, 0, 8)}), details=details)
    group.assert_coverage()
    assert group.samples == 1


def test_unhashable_sample_is_unmatched_without_losing_group_counters():
    group = CoverGroupDef("dynamic", (point(),)).instantiate("dut")
    group.sample({"value": []}, details=False)
    report = group.report()
    assert report["samples"] == 1 and report["points"][0]["unmatched"] == 1
    assert report["points"][0]["counts"]["zero"] == 0


def test_symbolic_exclusion_bounds_an_adversarial_mask_definition():
    # Every pigeon needs a hole, and every hole allows at most one pigeon.
    # Eight pigeons in seven holes leave no assignment outside these masks;
    # this compact schema requires a costly search and must hit the work limit.
    pigeons, holes = 8, 7
    width = pigeons * holes
    bins = {"domain": Bin.range(0, (1 << width) - 1)}
    for pigeon in range(pigeons):
        mask = sum(1 << (pigeon * holes + hole) for hole in range(holes))
        bins[f"absent_{pigeon}"] = Bin.ignore(Bin.masked(0, mask, width=width))
    for hole in range(holes):
        for left in range(pigeons):
            for right in range(left):
                mask = (1 << (left * holes + hole)) | (1 << (right * holes + hole))
                bins[f"collision_{hole}_{left}_{right}"] = Bin.ignore(Bin.masked(mask, mask, width=width))
    with pytest.raises(CoverageSchemaError, match="analysis is too complex"):
        CoverPointDef("bounded", bins)


def test_known_logic_values_progress_manual_transition_bins():
    group = CoverGroupDef("logic-transition", (CoverPointDef("value", {
        "zero": Bin.values(0), "path": Bin.transition(0, 1),
    }),)).instantiate("dut")
    for value in (0, 1):
        group.sample({"value": LogicValue(value, 0, 8)})
    group.assert_coverage()
