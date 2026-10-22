from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import tempfile
import unittest
import warnings
import pytest

from toffee import (
    Bin,
    CoverGroup,
    CoverageDatabase,
    CoverageMergeError,
    CoverageSchemaError,
    CoverGroupDef,
    CoverPointDef,
    CrossDef,
    Iff,
    IllegalBinError,
    IllegalPolicy,
)


def fetch_definition(*, goal: float = 100.0) -> CoverGroupDef:
    return CoverGroupDef(
        "fetch",
        (
            CoverPointDef(
                "target",
                {"itcm": Bin.values("itcm"), "biu": Bin.values("biu")},
            ),
            CoverPointDef(
                "alignment",
                {
                    "word": Bin.values(0),
                    "halfword": Bin.values(2, at_least=2),
                    "odd": Bin.illegal(Bin.masked(1, 1, width=2)),
                },
            ),
        ),
        (CrossDef("target_x_alignment", ("target", "alignment")),),
        goal=goal,
    )


class FunctionalCoverageTests(unittest.TestCase):
    def test_at_least_is_discrete_not_partial_credit(self) -> None:
        group = fetch_definition().instantiate("core0")
        group.sample({"target": "itcm", "alignment": 2})

        self.assertEqual(group.point_coverage("alignment"), 0.0)
        group.sample({"target": "itcm", "alignment": 2})
        self.assertEqual(group.point_coverage("alignment"), 50.0)

    def test_illegal_precedes_ignore_and_normal_and_commits_atomically(self) -> None:
        definition = CoverGroupDef(
            "priority",
            (
                CoverPointDef("before", {"seen": Bin.values(1)}),
                CoverPointDef(
                    "value",
                    {
                        "valid": Bin.values(0),
                        "normal": Bin.values(3),
                        "ignored": Bin.ignore(Bin.values(3)),
                        "illegal": Bin.illegal(Bin.values(3)),
                    },
                    overlap="allow",
                ),
            ),
        )
        group = definition.instantiate("dut")

        with self.assertRaises(IllegalBinError):
            group.sample({"before": 1, "value": 3}, metadata={"seed": 7})

        report = group.report()
        self.assertEqual(report["samples"], 1)
        before = next(item for item in report["points"] if item["name"] == "before")
        value = next(item for item in report["points"] if item["name"] == "value")
        self.assertEqual(before["counts"]["seen"], 1)
        self.assertEqual(value["counts"]["illegal"], 1)
        self.assertEqual(value["counts"]["normal"], 0)
        self.assertEqual(value["counts"]["ignored"], 0)
        self.assertEqual(report["illegal_hits"][0]["metadata"], {"seed": 7})

    def test_record_policy_defers_failure_until_assertion(self) -> None:
        group = fetch_definition().instantiate(
            "core0", illegal_policy=IllegalPolicy.RECORD
        )
        result = group.sample({"target": "itcm", "alignment": 1})
        self.assertEqual(len(result.illegal_hits), 1)
        with self.assertRaises(IllegalBinError):
            group.assert_coverage(0)

    def test_ignore_default_and_unmatched_diagnostics(self) -> None:
        definition = CoverGroupDef(
            "diagnostic",
            (
                CoverPointDef(
                    "with_default",
                    {
                        "zero": Bin.values(0),
                        "reserved": Bin.ignore(Bin.range(1, 3)),
                        "other": Bin.default(),
                    },
                ),
                CoverPointDef("without_default", {"zero": Bin.values(0)}),
            ),
        )
        group = definition.instantiate("dut")
        group.sample({"with_default": 2, "without_default": 9})
        group.sample({"with_default": 9, "without_default": 0})

        report = group.report()
        first, second = report["points"]
        self.assertEqual(first["ignored"], 1)
        self.assertEqual(first["counts"]["other"], 1)
        self.assertEqual(second["unmatched"], 1)
        self.assertEqual(group.point_coverage("with_default"), 0.0)

    def test_group_and_point_gates_are_diagnostic(self) -> None:
        definition = CoverGroupDef(
            "gated",
            (
                CoverPointDef(
                    "value",
                    {"one": Bin.values(1)},
                    iff=Iff.equals("point_enable", True),
                ),
            ),
            iff=Iff.equals("group_enable", True),
        )
        group = definition.instantiate("dut")
        first = group.sample({"value": 1, "point_enable": True, "group_enable": False})
        second = group.sample({"value": 1, "point_enable": False, "group_enable": True})

        self.assertTrue(first.gated)
        self.assertFalse(second.gated)
        report = group.report()
        self.assertEqual(report["gated"], 1)
        self.assertEqual(report["samples"], 1)
        self.assertEqual(report["points"][0]["gated"], 1)
        self.assertEqual(report["points"][0]["samples"], 0)

    def test_cross_gate_counts_only_eligible_samples(self) -> None:
        group = CoverGroupDef(
            "cross_gate",
            (CoverPointDef("a", {"one": Bin.values(1)}),
             CoverPointDef("b", {"one": Bin.values(1)})),
            (CrossDef("ab", ("a", "b"), iff=Iff.not_equals("mode", "idle")),),
        ).instantiate("dut")
        group.sample({"a": 1, "b": 1, "mode": "idle"})
        self.assertEqual(group.point_coverage("a"), 100)
        self.assertEqual(group.cross_coverage("ab"), 0)
        self.assertEqual(group.report()["crosses"][0]["gated"], 1)
        group.sample({"a": 1, "b": 1, "mode": "active"})
        group.assert_coverage()

    def test_weighted_goal_and_reset_preserve_definition(self) -> None:
        definition = CoverGroupDef(
            "weighted",
            (CoverPointDef("a", {"zero": Bin.values(0), "one": Bin.values(1)}, weight=3),
             CoverPointDef("b", {"zero": Bin.values(0), "one": Bin.values(1)}, weight=1)),
            goal=75,
        )
        group = definition.instantiate("dut")
        group.sample({"a": 0, "b": 0})
        self.assertFalse(group.covered)
        group.sample({"a": 1, "b": 0})
        self.assertEqual(group.coverage, 87.5)
        self.assertTrue(group.goal_met)
        self.assertFalse(group.covered)
        self.assertEqual(group.uncovered(), ("b.one",))
        with self.assertRaisesRegex(AssertionError, "item goals.*b"):
            group.assert_coverage()
        group.assert_coverage(per_item=False)
        group.reset()
        self.assertEqual(group.samples, 0)
        self.assertEqual(group.coverage, 0)
        self.assertIs(group.definition, definition)
        with self.assertRaises(AssertionError):
            group.assert_coverage()

    def test_cross_uses_bin_id_cartesian_product_for_overlap(self) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            definition = CoverGroupDef(
                "overlap",
                (
                    CoverPointDef(
                        "a",
                        {"all": Bin.range(0, 3), "even_two": Bin.values(2)},
                    ),
                    CoverPointDef("b", {"yes": Bin.values(True)}),
                ),
                (CrossDef("axb", ("a", "b")),),
            )
        group = definition.instantiate("dut")
        result = group.sample({"a": 2, "b": True})

        self.assertEqual(result.point_bins["a"], ("all", "even_two"))
        self.assertEqual(
            result.cross_bins["axb"], (("all", "yes"), ("even_two", "yes"))
        )
        self.assertEqual(group.cross_coverage("axb"), 100.0)

    def test_explicit_cross_ignore_and_illegal(self) -> None:
        definition = CoverGroupDef(
            "cross_control",
            (
                CoverPointDef("a", {"zero": Bin.values(0), "one": Bin.values(1)}),
                CoverPointDef("b", {"zero": Bin.values(0), "one": Bin.values(1)}),
            ),
            (
                CrossDef(
                    "axb",
                    ("a", "b"),
                    include=(("zero", "zero"), ("one", "one")),
                    ignore=(("zero", "one"),),
                    illegal=(("one", "zero"),),
                ),
            ),
        )
        group = definition.instantiate("dut", illegal_policy="record")
        group.sample({"a": 0, "b": 0})
        ignored = group.sample({"a": 0, "b": 1})
        illegal = group.sample({"a": 1, "b": 0})

        self.assertEqual(ignored.cross_bins["axb"], ())
        self.assertEqual(len(illegal.illegal_hits), 1)
        self.assertEqual(group.cross_coverage("axb"), 50.0)
        cross = group.report()["crosses"][0]
        self.assertEqual(cross["ignored"], 1)

    def test_auto_cross_limit_fails_at_schema_creation(self) -> None:
        with self.assertRaisesRegex(CoverageSchemaError, "max_auto_bins"):
            CoverGroupDef(
                "too_large",
                (
                    CoverPointDef("a", Bin.array("a", 0, 9)),
                    CoverPointDef("b", Bin.array("b", 0, 9)),
                ),
                (CrossDef("axb", ("a", "b"), max_auto_bins=50),),
            )

    def test_array_bins_partition_without_gaps(self) -> None:
        bins = Bin.array("quarter", 0, 9, count=4)
        point = CoverPointDef("value", bins)
        group = CoverGroupDef("array", (point,)).instantiate("dut")
        for value in range(10):
            group.sample({"value": value})

        counts = group.report()["points"][0]["counts"]
        self.assertEqual(sum(counts.values()), 10)
        self.assertEqual(list(counts.values()), [3, 3, 2, 2])
        self.assertEqual(group.coverage, 100.0)

    def test_wildcard_and_nested_source(self) -> None:
        @dataclass(frozen=True)
        class Command:
            opcode: int

        @dataclass(frozen=True)
        class Transaction:
            command: Command

        definition = CoverGroupDef(
            "opcode",
            (
                CoverPointDef(
                    "major",
                    {
                        "load": Bin.masked(0b0000011, 0b1111111, width=7),
                        "store": Bin.masked(0b0100011, 0b1111111, width=7),
                    },
                    source="command.opcode",
                ),
            ),
        )
        group = definition.instantiate("dut")
        group.sample(Transaction(Command(0b0000011)))
        group.sample(Transaction(Command(0b0100011)))
        self.assertEqual(group.coverage, 100.0)

    def test_schema_digest_is_order_independent_and_detects_change(self) -> None:
        left = CoverGroupDef(
            "stable",
            (CoverPointDef("p", {"one": Bin.values(1), "zero": Bin.values(0)}),),
        )
        right = CoverGroupDef(
            "stable",
            (CoverPointDef("p", {"zero": Bin.values(0), "one": Bin.values(1)}),),
        )
        changed = CoverGroupDef(
            "stable",
            (CoverPointDef("p", {"zero": Bin.values(0), "two": Bin.values(2)}),),
        )
        self.assertEqual(left.digest, right.digest)
        self.assertNotEqual(left.digest, changed.digest)

    def test_descriptions_round_trip_and_are_part_of_schema_identity(self) -> None:
        plain = CoverGroupDef(
            "documented",
            (
                CoverPointDef("request", {"seen": Bin.values(1)}),
                CoverPointDef("response", {"seen": Bin.values(1)}),
            ),
            (CrossDef("request_x_response", ("request", "response")),),
        )
        documented = CoverGroupDef(
            "documented",
            (
                CoverPointDef(
                    "request",
                    {"seen": Bin.values(1)},
                    description="A request was accepted.",
                ),
                CoverPointDef(
                    "response",
                    {"seen": Bin.values(1)},
                    description="A response was returned.",
                ),
            ),
            (
                CrossDef(
                    "request_x_response",
                    ("request", "response"),
                    description="Request and response occur in the same scenario.",
                ),
            ),
            description="End-to-end request coverage.",
        )

        restored = CoverGroupDef.from_dict(documented.to_dict())
        self.assertEqual(restored.to_dict(), documented.to_dict())
        self.assertEqual(restored.digest, documented.digest)
        self.assertEqual(restored.description, "End-to-end request coverage.")
        self.assertEqual(restored.points[0].description, "A request was accepted.")
        self.assertEqual(
            restored.crosses[0].description,
            "Request and response occur in the same scenario.",
        )
        self.assertNotEqual(plain.digest, documented.digest)

    def test_json_round_trip_and_same_instance_merge(self) -> None:
        first = fetch_definition().instantiate("core0")
        second = fetch_definition().instantiate("core0")
        first.sample({"target": "itcm", "alignment": 0})
        second.sample({"target": "biu", "alignment": 2})
        second.sample({"target": "biu", "alignment": 2})

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "functional-coverage.json"
            CoverageDatabase([first]).write_json(path)
            loaded = CoverageDatabase.read_json(path)
            loaded.merge(CoverageDatabase([second]))
            report = loaded["core0"].report()

        self.assertEqual(report["samples"], 3)
        self.assertEqual(loaded["core0"].point_coverage("target"), 100.0)
        self.assertEqual(loaded["core0"].point_coverage("alignment"), 100.0)
        json.dumps(loaded.report())

    def test_merge_rejects_same_instance_with_changed_schema(self) -> None:
        first = fetch_definition().instantiate("core0")
        changed = fetch_definition(goal=80).instantiate("core0")
        database = CoverageDatabase([first])
        with self.assertRaisesRegex(CoverageMergeError, "schema mismatch"):
            database.merge(CoverageDatabase([changed]))

    def test_type_report_merges_different_instances(self) -> None:
        first = fetch_definition().instantiate("core0")
        second = fetch_definition().instantiate("core1")
        first.sample({"target": "itcm", "alignment": 0})
        second.sample({"target": "biu", "alignment": 2})
        second.sample({"target": "biu", "alignment": 2})
        database = CoverageDatabase([first, second])

        type_report = database.type_reports()[0]
        merged = type_report["points"]
        self.assertEqual(type_report["instance"], "type:fetch")
        self.assertEqual(type_report["samples"], 3)
        self.assertEqual(next(x for x in merged if x["name"] == "target")["coverage"], 100.0)

    def test_extraction_failure_is_atomic(self) -> None:
        group = fetch_definition().instantiate("core0")
        with self.assertRaises(KeyError):
            group.sample({"target": "itcm"})
        self.assertEqual(group.report()["samples"], 0)
        self.assertTrue(all(item["samples"] == 0 for item in group.report()["points"]))

    def test_details_false_skips_result_without_changing_counters(self) -> None:
        definition = CoverGroupDef(
            "fast",
            (CoverPointDef("kind", {"one": Bin.values(1), "two": Bin.values(2)}),),
        )
        group = definition.instantiate("fast0")
        self.assertIsNone(group.sample({"kind": 1}, details=False))
        self.assertEqual(group.samples, 1)
        self.assertEqual(group.report()["points"][0]["counts"]["one"], 1)


if __name__ == "__main__":
    unittest.main()
@pytest.mark.parametrize("values,pattern,overlap,expected", [
    ([1, 2, 3], (1, 2, 3), True, 1),
    ([1, 9, 2, 3], (1, 2, 3), True, 0),
    ([1, 1, 1], (1, 1), True, 2),
    ([1, 1, 1], (1, 1), False, 1),
])
def test_adjacent_transition_bins(values, pattern, overlap, expected):
    group = CoverGroupDef("state", (CoverPointDef("value", {
        "path": Bin.transition(*pattern, overlap=overlap),
    }),)).instantiate("dut")
    for value in values:
        group.sample({"value": value})
    report = group.report()
    assert report["points"][0]["counts"]["path"] == expected
    assert report["schema"]["version"] == 2
    assert CoverGroup.from_report(report).report() == report


def test_transition_history_is_atomic_and_not_merged():
    schema = CoverGroupDef("state", (
        CoverPointDef("value", {"path": Bin.transition(1, 2)}),
        CoverPointDef("other", {"zero": Bin.values(0)}),
    ))
    group = schema.instantiate("dut")
    group.sample({"value": 1, "other": 0})
    with pytest.raises(KeyError):
        group.sample({"value": 9})
    group.sample({"value": 2, "other": 0})
    assert group.report()["points"][0]["counts"]["path"] == 1
    left, right = schema.instantiate("dut"), schema.instantiate("dut")
    left.sample({"value": 1, "other": 0})
    right.sample({"value": 2, "other": 0})
    left.merge(right)
    assert left.report()["points"][0]["counts"]["path"] == 0
