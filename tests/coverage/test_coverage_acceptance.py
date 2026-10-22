"""Regression cases for exclusion, acceptance, snapshot and evidence semantics."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from toffee import (
    Bin, CoverGroup, CoverGroupDef, CoverPointDef, CrossDef, CoverageDatabase,
    CoverageMergeError, CoverageSchemaError, IllegalBinError,
)


def definition(*, at_least=1):
    return CoverGroupDef("runs", (CoverPointDef("a", {
        "zero": Bin.values(0, at_least=at_least),
    }),))


@pytest.mark.parametrize("special", [Bin.ignore, Bin.illegal])
def test_empty_bins_leave_denominator_and_auto_cross(special):
    group = CoverGroupDef("exclusion", (
        CoverPointDef("a", {"zero": Bin.values(0), "one": Bin.values(1),
                             "special": special(Bin.values(1))}),
        CoverPointDef("b", {"zero": Bin.values(0)}),
    ), (CrossDef("ab", ("a", "b")),)).instantiate("dut", illegal_policy="record")
    group.sample({"a": 0, "b": 0})
    group.assert_coverage()
    group.sample({"a": 1, "b": 0})
    assert group.point_coverage("a") == group.cross_coverage("ab") == 100
    assert group.uncovered() == ()
    point, cross = group.report()["points"][0], group.report()["crosses"][0]
    assert set(point["excluded_bins"]) == {"one"}
    assert point["counts"]["one"] == 0
    assert [bin_["tuple"] for bin_ in cross["bins"]] == [["zero", "zero"]]
    assert group.covered == (special is Bin.ignore)


def test_partial_exclusion_keeps_bin_and_boolean_alias_is_not_lost():
    group = CoverGroupDef("partial", (CoverPointDef("a", {
        "both": Bin.values(0, 1), "ignored": Bin.ignore(Bin.values(1)),
    }),)).instantiate("dut")
    group.sample({"a": 1})
    assert group.coverage == 0 and not group.report()["points"][0]["excluded_bins"]
    group.sample({"a": 0})
    group.assert_coverage()
    alias = CoverGroupDef("aliases", (CoverPointDef("a", {
        "zero": Bin.values(0), "ignored": Bin.ignore(Bin.range(0, 0)),
    }),)).instantiate("dut")
    alias.sample({"a": False})
    alias.assert_coverage()


@pytest.mark.parametrize("normal,exclusions", [
    (Bin.range(-5, 5), (Bin.range(-5, -1), Bin.range(0, 5))),
    (Bin.range(0, (1 << 128) - 1),
     (Bin.masked(0, 1, width=128), Bin.masked(1, 1, width=128))),
    (Bin.masked(2, 2, width=128),
     (Bin.masked(2, 3, width=128), Bin.masked(3, 3, width=128))),
    (Bin.transition(2, 3), (Bin.values(3),)),
    (Bin.transition(2, 3), (Bin.transition(2, 3),)),
])
def test_union_and_transition_exclusions_without_enumeration(normal, exclusions):
    bins = {"dead": normal, "valid": Bin.values("valid")}
    bins.update({f"excluded{i}": Bin.ignore(bin_) for i, bin_ in enumerate(exclusions)})
    group = CoverGroupDef("union", (CoverPointDef("a", bins),)).instantiate("dut")
    assert set(group.report()["points"][0]["excluded_bins"]) == {"dead"}
    group.sample({"a": "valid"})
    group.assert_coverage()


def test_masks_keep_unbounded_tail_and_partial_negative_ranges():
    point = CoverPointDef("a", {"tail": Bin.masked(0, 1, width=4),
                                "excluded": Bin.ignore(Bin.range(0, 15))})
    group = CoverGroupDef("tail", (point,)).instantiate("dut")
    assert not group.report()["points"][0]["excluded_bins"]
    group.sample({"a": 16})
    group.assert_coverage()
    point = CoverPointDef("a", {"negative": Bin.range(-5, -1),
                                "excluded": Bin.ignore(Bin.range(-5, -2))})
    group = CoverGroupDef("partial", (point,)).instantiate("dut")
    group.sample({"a": -1})
    group.assert_coverage()


def test_no_eligible_bin_is_schema_error():
    with pytest.raises(CoverageSchemaError, match="normal"):
        CoverPointDef("a", {"dead": Bin.values(0), "ignored": Bin.ignore(Bin.values(0))})


def collision_definition():
    return CoverGroupDef("identity", (
        CoverPointDef("a", {"a": Bin.values(0), "a × b": Bin.values(1)}),
        CoverPointDef("b", {"b × c": Bin.values(0), "c": Bin.values(1)}),
    ), (CrossDef("ab", ("a", "b")),))


def test_cross_identity_separates_same_display_label():
    group = collision_definition().instantiate("dut")
    group.sample({"a": 0, "b": 0})
    report = group.report()
    cross = report["crosses"][0]
    assert cross["coverage"] == 25 and len(cross["counts"]) == 4
    assert len({tuple(bin_["tuple"]) for bin_ in cross["bins"]}) == 4
    assert len({bin_["label"] for bin_ in cross["bins"]}) == 3
    assert CoverGroup.from_report(json.loads(json.dumps(report))).report() == report
    group.sample({"a": 1, "b": 1})
    assert group.cross_coverage("ab") == 50


def test_merged_report_round_trip_preserves_order_and_snapshot():
    schema = CoverGroupDef("ordered", (
        CoverPointDef("z", {"zero": Bin.values(0), "one": Bin.values(1)}),
        CoverPointDef("a", {"zero": Bin.values(0), "one": Bin.values(1)}),
    ), (CrossDef("za", ("z", "a")),))
    left, right = schema.instantiate("dut"), schema.instantiate("dut")
    left.sample({"z": 0, "a": 0})
    right.sample({"z": 1, "a": 1})
    left.merge(right)
    report = left.report()
    assert CoverGroup.from_report(json.loads(json.dumps(report))).report() == report


def test_cross_cap_precedes_product_and_explicit_include_avoids_expansion(monkeypatch):
    points = tuple(CoverPointDef(name, {str(i): Bin.values(i) for i in range(40)})
                   for name in ("a", "b", "c"))
    def forbidden_product(*args):
        pytest.fail("unbounded cross product was allocated")
    monkeypatch.setattr("toffee.coverage.product", forbidden_product)
    with pytest.raises(CoverageSchemaError, match="max_auto_bins"):
        CoverGroupDef("cap", points, (CrossDef("abc", ("a", "b", "c"), max_auto_bins=10),))
    CoverGroupDef("explicit", points, (CrossDef("abc", ("a", "b", "c"),
                                              include=(("0", "0", "0"),)),))


def test_acceptance_gates_goals_and_illegal_separately():
    group = CoverGroupDef("goals", (
        CoverPointDef("a", {"zero": Bin.values(0)}, weight=9),
        CoverPointDef("b", {"zero": Bin.values(0)}),
    ), goal=90).instantiate("dut")
    group.sample({"a": 0, "b": 1})
    report = group.report()
    assert report["goal_met"] and not report["items_goal_met"] and not report["covered"]
    with pytest.raises(AssertionError, match="item goals.*b"):
        group.assert_coverage()
    group.assert_coverage(per_item=False)
    diagnostic = CoverGroupDef("zero_weight", (
        CoverPointDef("a", {"zero": Bin.values(0)}),
        CoverPointDef("b", {"zero": Bin.values(0)}, weight=0),
    )).instantiate("dut")
    diagnostic.sample({"a": 0, "b": 1})
    diagnostic.assert_coverage()
    illegal = CoverGroupDef("illegal", (CoverPointDef("a", {
        "zero": Bin.values(0), "bad": Bin.illegal(Bin.values(1)),
    }),)).instantiate("dut", illegal_policy="record")
    illegal.sample({"a": 0})
    illegal.sample({"a": 1})
    assert illegal.goal_met and not illegal.covered and illegal.report()["has_illegal"]
    assert not CoverageDatabase([illegal]).report()["covered"]
    with pytest.raises(IllegalBinError):
        illegal.assert_coverage(per_item=False)


def test_duplicate_and_cumulative_snapshots_cannot_create_hits():
    group = definition(at_least=2).instantiate("dut")
    group.sample({"a": 0})
    first = group.report()
    assert first == group.report()
    restored = CoverGroup.from_report(first)
    with pytest.raises(CoverageMergeError, match="duplicate|overlapping"):
        restored.merge(CoverGroup.from_report(first))
    assert restored.coverage == 0 and restored.samples == 1
    group.sample({"a": 0})
    assert group.report()["snapshot_id"] != first["snapshot_id"]
    with pytest.raises(CoverageMergeError, match="duplicate|overlapping"):
        restored.merge(group)
    independent = definition(at_least=2).instantiate("dut")
    independent.sample({"a": 0})
    restored.merge(independent)
    restored.assert_coverage()
    assert restored.samples == 2


def test_explicit_run_identity_and_reset():
    left = definition().instantiate("dut", run_id="seed-17")
    right = definition().instantiate("dut", run_id="seed-17")
    with pytest.raises(CoverageMergeError, match="duplicate|overlapping"):
        left.merge(right)
    auto = definition().instantiate("dut")
    before = CoverGroup.from_report(auto.report())
    auto.reset()
    auto.merge(before)
    left.reset()
    with pytest.raises(CoverageMergeError, match="duplicate|overlapping"):
        left.merge(right)


def test_database_merge_is_atomic_if_later_group_overlaps():
    left = CoverageDatabase([definition().instantiate(name) for name in ("first", "second")])
    new_first = definition().instantiate("first")
    new_first.sample({"a": 0})
    right = CoverageDatabase([new_first, CoverGroup.from_report(left["second"].report())])
    before = left.report()
    with pytest.raises(CoverageMergeError, match="duplicate|overlapping"):
        left.merge(right)
    assert left.report() == before


def test_normal_evidence_is_sparse_owned_and_merged_by_origin():
    context = {"test": "case-a", "seed": 17, "configuration": {"ways": 2}}
    group = definition().instantiate("dut", run_id="run-a", run_metadata=context,
                                     contract="output-before-scoreboard/v1")
    context["configuration"]["ways"] = 999
    first = {"tick": 2, "packet": {"tag": 1}}
    group.sample({"a": 0}, metadata=first, details=False)
    first["packet"]["tag"] = 999
    group.sample({"a": 0}, metadata={"tick": 4}, details=False)
    report = group.report()
    origin = report["active_origin"]
    assert report["origins"][origin]["metadata"]["configuration"] == {"ways": 2}
    proof = report["points"][0]["provenance"]["zero"][origin]
    assert proof == {"count": 2, "first": {"tick": 2, "packet": {"tag": 1}}, "last": {"tick": 4}}
    proof["first"]["packet"]["tag"] = 777
    assert group.report()["points"][0]["provenance"]["zero"][origin]["first"]["packet"]["tag"] == 1
    other = definition().instantiate("dut", run_id="run-b", run_metadata={"seed": 23},
                                     contract="output-before-scoreboard/v1")
    other.sample({"a": 0})
    group.merge(other)
    evidence = group.report()["points"][0]["provenance"]["zero"]
    assert len(evidence) == 2 and sum(item["count"] for item in evidence.values()) == 3
    with pytest.raises(CoverageMergeError, match="contract"):
        group.merge(definition().instantiate("dut", contract="after-scoreboard/v1"))


def test_only_current_report_and_database_formats_are_accepted():
    group = definition().instantiate("dut")
    report = group.report()
    database = CoverageDatabase([group]).report()
    for version in (None, 1, 3):
        with pytest.raises(CoverageMergeError, match="unsupported coverage report version"):
            CoverGroup.from_report(dict(report, report_version=version))
        with pytest.raises(CoverageMergeError, match="unsupported.*database format"):
            CoverageDatabase.from_dict(dict(database, version=version))


def resign(report):
    result = deepcopy(report)
    result.pop("snapshot_id")
    result["snapshot_id"] = sha256(json.dumps(result, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return result


@pytest.mark.parametrize("problem,message", [
    ("schema", "schema digest"),
    ("active-origin", "active origin"),
    ("run-id", "run identity"),
    ("bin-identity", "evidence identity"),
    ("origin-identity", "evidence identity"),
    ("nonpositive", "positive integers"),
    ("inflated", "exceeds bin hit count"),
    ("counter-identity", "counters do not match schema"),
    ("duplicate-item", "items do not match schema"),
])
def test_corrupted_snapshot_evidence_cannot_be_restored(problem, message):
    group = definition().instantiate("dut", run_id="seed-17")
    group.sample({"a": 0}, metadata={"tick": 2})
    report = group.report()
    point = report["points"][0]
    origin = report["active_origin"]
    if problem == "schema":
        report["schema_digest"] = "wrong"
    elif problem == "active-origin":
        report["active_origin"] = "missing"
    elif problem == "run-id":
        report["origins"][origin]["run_id"] = ""
    elif problem == "bin-identity":
        point["provenance"]["missing"] = point["provenance"].pop("zero")
    elif problem == "origin-identity":
        point["provenance"]["zero"]["missing"] = point["provenance"]["zero"].pop(origin)
    elif problem in ("nonpositive", "inflated"):
        point["provenance"]["zero"][origin]["count"] = 0 if problem == "nonpositive" else 2
    elif problem == "counter-identity":
        point["counts"] = {"missing": 1}
    else:
        report["points"].append(deepcopy(point))
    with pytest.raises(CoverageMergeError, match=message):
        CoverGroup.from_report(resign(report))


def test_instance_merge_and_duplicate_database_instance_are_explicit():
    left, right = definition().instantiate("left"), definition().instantiate("right")
    right.sample({"a": 0})
    before = left.report()
    with pytest.raises(CoverageMergeError, match="instance mismatch"):
        left.merge(right)
    assert left.report() == before
    left.merge(right, require_instance=False)
    left.assert_coverage()
    database = CoverageDatabase([left])
    with pytest.raises(ValueError, match="duplicate coverage instance"):
        database.add(left)


@pytest.mark.asyncio
async def test_merge_without_optional_pattern_diagnostics_keeps_existing_summary():
    from types import SimpleNamespace
    from toffee import ClockCycles, Execution, MemoryBackend, RisingEdge

    backend = MemoryBackend(object())
    left, right = definition().instantiate("dut"), definition().instantiate("dut")
    for candidate in (left, right):
        candidate.bind(trigger=RisingEdge(backend.clock), fields={"a": SimpleNamespace(value=0)},
                       strategy="python", diagnostics="summary")
        async with Execution(backend, coverage=[candidate]):
            await ClockCycles(backend.clock, 1)
    before = left.report()["diagnostics"]
    archive = right.report()
    archive.pop("diagnostics")
    restored = CoverGroup.from_report(resign(archive))
    left.merge(restored)
    assert left.samples == 2 and left.report()["diagnostics"] == before


def test_snapshot_changes_are_detected():
    report = definition().instantiate("dut").report()
    report["points"][0]["counts"]["zero"] = 10
    with pytest.raises(CoverageMergeError, match="snapshot digest"):
        CoverGroup.from_report(report)


def test_invalid_counts_and_missing_items_are_rejected():
    report = definition().instantiate("dut").report()
    report["points"][0]["counts"]["zero"] = -1
    with pytest.raises(CoverageMergeError, match="nonnegative integers"):
        CoverGroup.from_report(resign(report))
    report["points"] = []
    with pytest.raises(CoverageMergeError, match="items.*schema"):
        CoverGroup.from_report(resign(report))


def test_symbolic_integer_exclusion_agrees_with_small_exhaustive_domains():
    import random
    from toffee._coverage_exclusions import _integer_empty
    rng = random.Random(17)
    for _ in range(200):
        matcher = (Bin.masked(rng.randrange(16), rng.randrange(16), width=4).matcher
                   if rng.randrange(2) else Bin.range(rng.randrange(-4, 4), rng.randrange(4, 16)).matcher)
        exclusions = []
        for _ in range(rng.randrange(1, 5)):
            spec = (Bin.masked(rng.randrange(16), rng.randrange(16), width=4)
                    if rng.randrange(2) else Bin.range(rng.randrange(-4, 4), rng.randrange(4, 16)))
            exclusions.append(spec.matcher)
        # The extra high-bit domain represents the unbounded mask tail beyond
        # every finite range. This reference enumerates only small test domains.
        reference = all(any(other.matches(value) for other in exclusions)
                        for value in range(-4, 32) if matcher.matches(value))
        assert _integer_empty(matcher, exclusions) == reference
