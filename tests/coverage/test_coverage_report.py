from __future__ import annotations

import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import unittest
import shutil

import pytest

from toffee import (
    Bin,
    CoverageDatabase,
    CoverGroupDef,
    CoverPointDef,
    CrossDef,
    CoverageMergeError,
    generate_unified_coverage_report,
    generate_unified_coverage_site,
    parse_lcov,
)
from toffee.coverage_report import main


@pytest.mark.parametrize("text,message", [
    ("SF:/rtl/test.sv\nDA:1\n", "malformed LCOV DA"),
    ("TN:no sources\n", "no source records"),
])
def test_invalid_lcov_reports_errors(tmp_path, text, message):
    path = tmp_path / "invalid.info"
    path.write_text(text)
    with pytest.raises(ValueError, match=message):
        parse_lcov(path)


def test_lcov_handles_empty_and_disjoint_uncovered_ranges(tmp_path):
    path = tmp_path / "ranges.info"
    path.write_text("SF:/rtl/covered.sv\nDA:1,1\nend_of_record\n"
                    "SF:/rtl/gaps.sv\nDA:1,0\nDA:3,0\nDA:4,0\nDA:6,0\nend_of_record\n")
    report = parse_lcov(path)
    assert report["files"][0]["uncovered_lines"] == []
    assert report["files"][1]["uncovered_lines"] == ["1", "3-4", "6"]


@pytest.mark.parametrize("argument", ["missing-equals", "=path", "name="])
def test_report_cli_rejects_malformed_line_input(argument, capsys):
    with pytest.raises(SystemExit) as caught:
        main(["--line-coverage", argument, "--output", "unused.html"])
    assert caught.value.code == 2 and "NAME=PATH" in capsys.readouterr().err


def test_report_cli_requires_output(capsys):
    with pytest.raises(SystemExit) as caught:
        main([])
    assert caught.value.code == 2 and "--output or --site" in capsys.readouterr().err


@pytest.mark.skipif(shutil.which("genhtml") is None, reason="requires genhtml")
def test_cli_generates_both_outputs_and_protects_unowned_directory(tmp_path):
    group = CoverGroupDef("cli", (CoverPointDef("a", {"zero": Bin.values(0)}),)).instantiate("dut")
    group.sample({"a": 0})
    functional = CoverageDatabase([group]).write_json(tmp_path / "functional.json")
    line = tmp_path / "line.info"
    source = tmp_path / "test.sv"
    source.write_text("module test; endmodule\n")
    line.write_text(f"SF:{source}\nDA:1,1\nend_of_record\n")
    html, site = tmp_path / "coverage.html", tmp_path / "site"
    assert main(["--functional", str(functional), "--line-coverage", f"rtl={line}",
                 "--output", str(html), "--site", str(site)]) == 0
    assert html.is_file() and (site / "index.html").is_file()
    (site / "stale.html").write_text("stale output")
    generate_unified_coverage_site(functional_paths=[functional], output_dir=site)
    assert not (site / "stale.html").exists()
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    kept = foreign / "keep.txt"
    kept.write_text("user data")
    with pytest.raises(FileExistsError, match="non-Toffee directory"):
        generate_unified_coverage_site(functional_paths=[functional], output_dir=foreign)
    assert kept.read_text() == "user data"


@pytest.mark.parametrize("missing", [False, True])
def test_site_reports_genhtml_failures(tmp_path, monkeypatch, missing):
    import subprocess

    line = tmp_path / "line.info"
    line.write_text("SF:/rtl/test.sv\nDA:1,1\nend_of_record\n")

    def broken(command, **kwargs):
        if missing:
            raise FileNotFoundError("missing genhtml")
        raise subprocess.CalledProcessError(2, command, stderr="source file unavailable\n")

    monkeypatch.setattr(subprocess, "run", broken)
    with pytest.raises(RuntimeError, match="required" if missing else "source file unavailable"):
        generate_unified_coverage_site(line_coverage=[("rtl", line)], output_dir=tmp_path / "site")


def test_module_entry_point_generates_report(tmp_path, monkeypatch):
    import runpy
    import sys

    group = CoverGroupDef("module", (CoverPointDef("a", {"zero": Bin.values(0)}),)).instantiate("dut")
    functional = CoverageDatabase([group]).write_json(tmp_path / "functional.json")
    output = tmp_path / "coverage.html"
    monkeypatch.setattr(sys, "argv", ["toffee.coverage_report", "--functional", str(functional), "--output", str(output)])
    with pytest.warns(RuntimeWarning, match="found in sys.modules"), pytest.raises(SystemExit) as caught:
        runpy.run_module("toffee.coverage_report", run_name="__main__")
    assert caught.value.code == 0 and output.is_file()


def test_site_shows_acceptance_exclusions_cross_kinds_and_evidence(tmp_path):
    group = CoverGroupDef("report", (
        CoverPointDef("a", {"zero": Bin.values(0), "one": Bin.values(1),
                             "ignored": Bin.ignore(Bin.values(1))}),
        CoverPointDef("b", {"zero": Bin.values(0), "one": Bin.values(1)}),
    ), (CrossDef("ab", ("a", "b"), illegal=(("zero", "one"),)),)).instantiate(
        "dut", run_id="run-a", run_metadata={"test": "case<script>", "seed": 17},
        illegal_policy="record",
    )
    group.sample({"a": 0, "b": 0}, metadata={"tick": 2})
    group.sample({"a": 0, "b": 1}, metadata={"tick": 4})
    assert group.coverage == 100 and not group.covered
    path = CoverageDatabase([group]).write_json(tmp_path / "functional.json")
    generate_unified_coverage_site(functional_paths=[path], output_dir=tmp_path / "site")
    root, = (tmp_path / "site/functional/groups").glob("*")
    assert "Coverage acceptance: not accepted" in (root / "index.html").read_text()
    point_pages = [path.read_text() for path in (root / "points").glob("*.html")]
    assert any("excluded" in page for page in point_pages)
    assert any("case&lt;script&gt;" in page and "Run evidence" in page for page in point_pages)
    cross, = (root / "crosses").glob("*.html")
    document = cross.read_text()
    assert "illegal" in document and "[&quot;zero&quot;, &quot;one&quot;]" in document
    with pytest.raises(CoverageMergeError, match="duplicate|overlapping"):
        generate_unified_coverage_report(functional_paths=[path, path], output=tmp_path / "bad.html")


class CoverageReportTests(unittest.TestCase):
    def test_lcov_deduplicates_records_and_reports_uncovered_ranges(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "code.info"
            path.write_text(
                "SF:/rtl/a.sv\nDA:10,2\nDA:11,0\nDA:12,0\nend_of_record\n"
                "SF:/rtl/a.sv\nDA:10,3\nDA:13,1\nend_of_record\n"
                "SF:/rtl/b.sv\nLF:4\nLH:3\nend_of_record\n",
                encoding="utf-8",
            )
            report = parse_lcov(path, name="sim")
        self.assertEqual(report["lines_found"], 8)
        self.assertEqual(report["lines_hit"], 5)
        self.assertEqual(report["files"][0]["uncovered_lines"], ["11-12"])

    def test_generates_self_contained_combined_html_and_escapes_payload(self) -> None:
        definition = CoverGroupDef(
            "fetch<script>",
            (CoverPointDef("kind", {"ok": Bin.values("ok")}),),
            description="Validate <fetch> behavior.",
        )
        group = definition.instantiate("ifu0")
        group.sample({"kind": "ok"}, details=False)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            functional = CoverageDatabase([group]).write_json(root / "functional.json")
            lcov = root / "line.info"
            lcov.write_text(
                "TN:test\nSF:/rtl/top.sv\nDA:1,1\nDA:2,0\nend_of_record\n",
                encoding="utf-8",
            )
            output = generate_unified_coverage_report(
                functional_paths=[functional],
                line_coverage=[("Verilator", lcov)],
                output=root / "coverage.html",
                title="DUT <coverage>",
            )
            document = output.read_text(encoding="utf-8")
        self.assertIn("DUT &lt;coverage&gt;", document)
        self.assertIn('\"format\":\"xreactor-unified-coverage\"', document)
        self.assertIn('\"name\":\"Verilator\"', document)
        self.assertNotIn("fetch<script>", document)
        self.assertNotIn("Validate <fetch> behavior.", document)
        self.assertNotIn("@@REPORT_JSON@@", document)

    def test_generates_functional_directory_and_group_pages(self) -> None:
        definition = CoverGroupDef(
            "fetch",
            (
                CoverPointDef(
                    "kind",
                    {"ok": Bin.values("ok"), "err": Bin.values("err")},
                    description="Classifies the fetch result.",
                ),
            ),
            description="Covers end-to-end fetch behavior.",
        )
        group = definition.instantiate("core0.ifu")
        group.sample({"kind": "ok"}, details=False)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            functional = CoverageDatabase([group]).write_json(root / "functional.json")
            site = root / "site"
            index = generate_unified_coverage_site(
                functional_paths=[functional],
                output_dir=site,
                title="DUT coverage",
            )
            group_pages = list((site / "functional" / "groups").glob("*/index.html"))
            group_document = group_pages[0].read_text(encoding="utf-8")
            point_pages = list((site / "functional" / "groups").glob("*/points/*.html"))
            point_document = point_pages[0].read_text(encoding="utf-8")
            # Regeneration is permitted only because the site carries its marker.
            generate_unified_coverage_site(
                functional_paths=[functional], output_dir=site, title="DUT coverage"
            )
            self.assertTrue(index.is_file())
            self.assertTrue((site / "functional" / "index.html").is_file())
            self.assertTrue((site / "line" / "index.html").is_file())
        self.assertEqual(len(group_pages), 1)
        self.assertEqual(len(point_pages), 1)
        self.assertIn("points/", group_document)
        self.assertIn("Covers end-to-end fetch behavior.", group_document)
        self.assertIn("Classifies the fetch result.", group_document)
        self.assertIn("Classifies the fetch result.", point_document)
        self.assertIn("uncovered", point_document)
        self.assertIn("Matcher / definition", point_document)



def test_cli_merges_partial_case_coverage_into_html_and_site(tmp_path):
    definition = CoverGroupDef("responses", (CoverPointDef(
        "kind", {name: Bin.values(name) for name in ("first", "shared", "last")},
    ),))
    paths = []
    for index, observed in enumerate((("first", "shared"), ("shared", "last"))):
        group = definition.instantiate("dut.responses")
        for kind in observed:
            group.sample({"kind": kind})
        assert group.coverage == pytest.approx(200 / 3)
        paths.append(CoverageDatabase([group]).write_json(tmp_path / f"case-{index}.json"))
    single, site = tmp_path / "coverage.html", tmp_path / "site"
    assert main([
        "--functional", str(paths[0]), "--functional", str(paths[1]),
        "--output", str(single), "--site", str(site),
    ]) == 0
    document = single.read_text()
    payload = re.search(r'<script id="coverage-data" type="application/json">(.*?)</script>', document, re.S)
    model = json.loads(payload.group(1))
    assert model["functional"]["coverage"] == 100
    group, = model["functional"]["groups"]
    assert group["points"][0]["counts"] == {"first": 1, "shared": 2, "last": 1}
    assert (site / "functional" / "index.html").is_file()
    overview = (site / "index.html").read_text()
    assert "100.00%" in overview
    assert "Functional-point completion" not in document
    assert "Functional-point completion" not in overview
    assert "features/index.html" not in overview
    assert not (site / "features").exists()


@pytest.mark.parametrize("layout", ["single", "site"])
def test_report_requires_coverage_input(tmp_path, layout):
    with pytest.raises(ValueError, match="at least one functional or line-coverage input"):
        if layout == "single":
            generate_unified_coverage_report(output=tmp_path / "coverage.html")
        else:
            generate_unified_coverage_site(output_dir=tmp_path / "site")


@pytest.mark.asyncio
@pytest.mark.parametrize("diagnostics", ["off", "summary"])
async def test_pattern_diagnostics_are_optional_in_reports(tmp_path, diagnostics):
    from types import SimpleNamespace
    from toffee import ClockCycles, Execution, MemoryBackend, RisingEdge
    clock = object()
    backend = MemoryBackend(clock)
    value = SimpleNamespace(value=1, width=8)
    group = CoverGroupDef("progress", (CoverPointDef("value", {
        "path": Bin.transition(1, 2),
    }),)).instantiate("dut").bind(trigger=RisingEdge(clock), fields={"value": value},
                                 diagnostics=diagnostics)
    try:
        async with Execution(backend, coverage=[group]):
            await ClockCycles(clock, 1)
        artifact = CoverageDatabase([group]).write_json(tmp_path / "coverage.json")
        generate_unified_coverage_site(functional_paths=[artifact], output_dir=tmp_path / "site")
        pages = list((tmp_path / "site/functional/groups").glob("*/index.html"))
        html = pages[0].read_text()
        assert ("Pattern diagnostics (optional)" in html) == (diagnostics == "summary")
        if diagnostics == "summary":
            assert "Unfinished At Close" in html and "value.path" in html
        single = generate_unified_coverage_report(functional_paths=[artifact], output=tmp_path / "report.html")
        payload = re.search(r'<script id="coverage-data" type="application/json">(.*?)</script>', single.read_text(), re.S)
        saved = json.loads(payload.group(1))["functional"]["groups"][0]
        assert saved["diagnostics"] == group.report()["diagnostics"]
        assert saved["coverage"] == 0
    finally:
        backend.close()


# Exercise real source rendering as well as the renderer-independent model.
# genhtml is an optional report dependency; CI installs lcov explicitly.
@pytest.mark.parametrize("layout", ["single", "nested"])
def test_real_genhtml_resolves_sources_and_links_back(tmp_path, layout):
    if shutil.which("genhtml") is None:
        pytest.skip("source coverage pages require genhtml (lcov)")
    sources = [tmp_path / "counter.sv"]
    if layout == "nested":
        sources.append(tmp_path / "child" / "other.sv")
    trace = tmp_path / "line.info"
    records = []
    for source in sources:
        source.parent.mkdir(exist_ok=True)
        source.write_text("module counter;\nendmodule\n")
        records.append(f"SF:{source}\nDA:1,1\nDA:2,0\nLF:2\nLH:1\nend_of_record\n")
    trace.write_text("".join(records))
    site = tmp_path / "report"
    index = generate_unified_coverage_site(line_coverage=[("RTL", trace)], output_dir=site)
    pages = list((site / "line").rglob("*.gcov.html"))
    assert index.is_file()
    assert len(pages) == len(sources)
    for page in pages:
        document = page.read_text()
        assert "module counter" in document
        assert "Unified coverage overview" in document
