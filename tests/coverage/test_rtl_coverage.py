from __future__ import annotations

import json

import pytest

from toffee import Bin, CoverageDatabase, CoverGroupDef, CoverPointDef
from toffee.coverage_report import main
from toffee.coverage.rtl import from_verilator_dat, read_report, write_metric_reports


def _write_dat(path, points):
    with path.open("w", encoding="utf-8") as output:
        output.write("# SystemC::Coverage-3\n")
        for fields, count in points:
            key = "".join(f"\x01{k}\x02{v}" for k, v in fields.items())
            output.write(f"C '{key}' {count}\n")
    return path


def test_dat_metrics_do_not_inflate_line_counts(tmp_path):
    path = _write_dat(tmp_path / "coverage.dat", [
        ({"f": "dut.sv", "l": 10, "S": "11-13", "page": "v_line/Dut"}, 2),
        ({"f": "dut.sv", "l": 13, "S": "14-15", "t": "line"}, 0),
        ({"f": "dut.sv", "l": 15, "t": "branch"}, 2),
        ({"f": "dut.sv", "l": 20, "t": "toggle"}, 0),
        ({"f": "dut.sv", "l": 21, "t": "expr"}, 3),
        ({"f": "dut.sv", "l": 22, "t": "fsm_state"}, 2),
        ({"f": "dut.sv", "l": 22, "t": "fsm_arc"}, 0),
        ({"f": "other.sv", "l": 10, "t": "line"}, 1),
    ])
    metrics = from_verilator_dat(path, name="regression")["metrics"]
    assert (metrics["line"]["covered"], metrics["line"]["total"]) == (5, 7)
    assert (metrics["block"]["covered"], metrics["block"]["total"]) == (2, 3)
    assert metrics["statement"]["status"] == "unavailable"
    assert metrics["branch"]["total"] == metrics["expression"]["total"] == 1
    assert metrics["toggle"]["covered"] == 0
    assert metrics["fsm_state"]["covered"] == 1
    assert metrics["fsm_transition"]["status"] == "measured"
    assert metrics["fsm_transition"]["covered"] == 0


def test_dat_does_not_publish_incomplete_source_line_projection(tmp_path):
    path = _write_dat(tmp_path / "coverage.dat", [
        ({"f": "dut.sv", "l": 3, "t": "line"}, 1),
        ({"f": "", "l": 0, "t": "line"}, 0),
    ])
    metrics = from_verilator_dat(path, name="regression")["metrics"]
    assert metrics["block"]["total"] == 2
    assert metrics["line"]["status"] == "unavailable"


def test_offline_cli_normalizes_three_inputs_and_renders_both_layouts(tmp_path):
    dat = _write_dat(tmp_path / "coverage.dat", [
        ({"f": "a.sv", "l": 7, "t": "line"}, 1),
        ({"f": "a.sv", "l": 8, "t": "toggle"}, 2),
    ])
    vcs = tmp_path / "vcs.json"
    vcs.write_text(json.dumps({"schema_version": 1, "success": True, "simulator": "vcs",
                               "query": {"tests": ["one"]}, "metrics": {
                                   "line": {"covered": 1, "total": 2},
                                   "fsm": {"covered": 0, "total": 1},
                                   "branch": {"covered": 0, "total": 0},
                                   "toggle": {"covered": 1, "total": 2},
                               }}))
    uvs = tmp_path / "coverage.json"
    uvs.write_text(json.dumps({"summary": {"summaryData": {
        "stmt": {"cd": 3, "un+cd": 5}, "fsm": {"cd": 2, "un+cd": 3},
        "tgl": {"cd": 2, "un+cd": 4},
    }}}))
    data, html, site = (tmp_path / name for name in ("rtl-data", "coverage.html", "site"))
    assert main(["--verilator-dat", f"RTL={dat}", "--vcs-json", f"VCS<script>={vcs}",
                 "--uvs-json", f"UVS={uvs}", "--rtl-output", str(data)]) == 0
    assert {path.name for path in data.iterdir()} == {
        "line.json", "statement.json", "block.json", "branch.json",
        "condition.json", "expression.json", "toggle.json", "fsm.json",
    }
    toggle = json.loads((data / "toggle.json").read_text())
    assert toggle["format"] == "toffee-rtl-coverage-metric"
    assert toggle["version"] == 1 and toggle["metric"] == "toggle"
    for row, simulator, counts in zip(toggle["datasets"], ("verilator", "vcs", "uvs"),
                                       ((1, 1), (1, 2), (2, 4)), strict=True):
        assert row["simulator"] == simulator
        assert row["metrics"].keys() == {"toggle"}
        metric = row["metrics"]["toggle"]
        assert metric.keys() == {"covered", "total", "unit", "basis", "status"}
        assert (metric["covered"], metric["total"]) == counts
    fsm = json.loads((data / "fsm.json").read_text())
    assert all(row["metrics"].keys() == {"fsm_state", "fsm_transition", "fsm_native"}
               for row in fsm["datasets"])
    line_only = read_report(data / "line.json")
    assert line_only[0]["metrics"]["line"]["total"] == 1
    assert line_only[0]["metrics"]["block"]["status"] == "unavailable"
    ver, vcs_data, uvs_data = read_report(data)
    assert [row["simulator"] for row in (ver, vcs_data, uvs_data)] == ["verilator", "vcs", "uvs"]
    assert vcs_data["query"]["tests"] == ["one"]
    assert vcs_data["metrics"]["line"]["covered"] is None
    assert vcs_data["metrics"]["statement"]["total"] == 2
    assert vcs_data["metrics"]["fsm_transition"]["total"] == 1
    assert vcs_data["metrics"]["branch"]["status"] == "no_targets"
    assert uvs_data["metrics"]["statement"]["covered"] == 3
    assert uvs_data["metrics"]["fsm_native"]["total"] == 3
    assert uvs_data["metrics"]["branch"]["status"] == "unavailable"
    group = CoverGroupDef("example", (CoverPointDef("input", {"low": Bin.values(0)}),)).instantiate("dut")
    group.sample({"input": 0})
    functional = CoverageDatabase([group]).write_json(tmp_path / "functional.json")
    assert main(["--rtl-data", str(data), "--functional", str(functional),
                 "--output", str(html), "--site", str(site)]) == 0
    for path in (html, site / "index.html"):
        document = path.read_text()
        assert "RTL coverage summary" in document
        assert "No targets reported" in document
        assert "Not available in this input" in document
        assert "0.00%" in document
        assert "VCS&lt;script&gt;" in document and "VCS<script>" not in document
        assert "Functional" in document
    assert '"instance":"dut"' in html.read_text()
    assert (site / "functional" / "index.html").is_file()


def test_cli_rejects_invalid_data_and_does_not_replace_input(tmp_path, capsys):
    path = tmp_path / "rtl.json"
    original = '{"format":"toffee-rtl-coverage","version":42}'
    path.write_text(original)
    with pytest.raises(SystemExit) as caught:
        main(["--rtl-data", str(path), "--rtl-output", str(path)])
    assert caught.value.code == 2 and "cannot replace an input" in capsys.readouterr().err
    assert path.read_text() == original
    with pytest.raises(SystemExit) as caught:
        main(["--rtl-data", str(path), "--output", str(tmp_path / "out.html")])
    assert caught.value.code == 2 and "unsupported RTL coverage format" in capsys.readouterr().err
    assert not (tmp_path / "out.html").exists()


def test_metric_reports_roundtrip_keeps_same_named_datasets_separate(tmp_path):
    dat = _write_dat(tmp_path / "coverage.dat", [({"f": "dut.sv", "l": 3, "t": "line"}, 1)])
    first = from_verilator_dat(dat, name="dut")
    _write_dat(dat, [({"f": "dut.sv", "l": 3, "t": "line"}, 0)])
    second = from_verilator_dat(dat, name="dut")
    out = tmp_path / "metrics"
    write_metric_reports(out, [first, second])
    line_path = out / "line.json"
    saved = json.loads(line_path.read_text())
    saved["datasets"].reverse()
    line_path.write_text(json.dumps(saved))
    assert read_report(out) == [first, second]
    with pytest.raises(FileExistsError):
        write_metric_reports(out, [second])
    assert read_report(out) == [first, second]


def test_cli_cannot_replace_a_metric_file_inside_the_input_directory(tmp_path, capsys):
    dat = _write_dat(tmp_path / "coverage.dat", [({"f": "dut.sv", "l": 3, "t": "line"}, 1)])
    out = tmp_path / "metrics"
    write_metric_reports(out, [from_verilator_dat(dat, name="dut")])
    file = out / "line.json"
    original = file.read_bytes()
    with pytest.raises(SystemExit) as caught:
        main(["--rtl-data", str(out), "--output", str(file)])
    assert caught.value.code == 2 and "cannot replace an input" in capsys.readouterr().err
    assert file.read_bytes() == original


def test_metric_report_rejects_conflicting_dataset_metadata(tmp_path):
    dat = _write_dat(tmp_path / "coverage.dat", [({"f": "dut.sv", "l": 3, "t": "line"}, 1)])
    out = tmp_path / "metrics"
    write_metric_reports(out, [from_verilator_dat(dat, name="dut")])
    file = out / "line.json"
    document = json.loads(file.read_text())
    document["datasets"][0]["name"] = "different"
    file.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="inconsistent RTL coverage dataset ID"):
        read_report(out)


def test_metric_directory_rejects_results_from_a_different_export(tmp_path):
    dat = _write_dat(tmp_path / "coverage.dat", [({"f": "dut.sv", "l": 3, "t": "line"}, 1)])
    first, second = tmp_path / "first", tmp_path / "second"
    write_metric_reports(first, [from_verilator_dat(dat, name="dut")])
    _write_dat(dat, [({"f": "dut.sv", "l": 3, "t": "line"}, 0)])
    write_metric_reports(second, [from_verilator_dat(dat, name="dut")])
    file = first / "line.json"
    file.write_bytes((second / file.name).read_bytes())
    assert read_report(file)[0]["metrics"]["line"]["covered"] == 0
    with pytest.raises(ValueError, match="different exports"):
        read_report(first)


def test_can_read_previous_single_file_summary(tmp_path):
    dat = _write_dat(tmp_path / "coverage.dat", [({"f": "dut.sv", "l": 3, "t": "line"}, 1)])
    dataset = from_verilator_dat(dat, name="dut")
    path = tmp_path / "old.json"
    path.write_text(json.dumps({"format": "toffee-rtl-coverage", "version": 1, "datasets": [dataset]}))
    assert read_report(path) == [dataset]
