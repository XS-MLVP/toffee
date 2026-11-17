"""Opt-in real RTL regression with the matching aggregate Picker/xcomm build."""

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples/integration/aggregates"


@pytest.mark.parametrize("access", ("dpi", "mem_direct", "vpi"))
def test_generated_picker_aggregates(tmp_path, access):
    if os.environ.get("TOFFEE_AGGREGATE_RTL") != "1":
        pytest.skip("set TOFFEE_AGGREGATE_RTL=1 to build and run the Picker aggregate example")
    picker = os.environ.get("PICKER_BIN") or shutil.which("picker")
    template = os.environ.get("PICKER_TEMPLATE")
    assert picker, "TOFFEE_AGGREGATE_RTL requires PICKER_BIN or picker on PATH"
    assert shutil.which("verilator"), "TOFFEE_AGGREGATE_RTL requires Verilator 5.048+ (VPI unpacked structs: 5.052+)"
    package = tmp_path / "AggregateSignals"
    args = [picker, "export", str(EXAMPLE / "AggregateSignals.sv"),
            "--sname", "AggregateSignals", "--tdir", str(package),
            "--lang", "python", "--sim", "verilator", "--rw", "mem_direct" if access == "mem_direct" else "dpi",
            "--autobuild", "false", "-V", "-Wno-fatal"]
    if access == "vpi":
        args.append("--vpi")
    if template:
        args.extend(("--sdir", template))
    env = {**os.environ, "PYTHONPATH": os.pathsep.join((str(tmp_path), str(ROOT / "src"), os.environ.get("PYTHONPATH", "")))}

    def run(command):
        result = subprocess.run(command, cwd=tmp_path, env=env,
                                text=True, capture_output=True, timeout=600)
        assert result.returncode == 0, result.stdout + result.stderr
        return result

    run(args)
    run(["make", "-C", str(package), "NPROC=4"])
    assert (package / "AggregateSignals.xscfg").is_file()
    assert not list(package.rglob("signals.json"))
    result = run([sys.executable, str(EXAMPLE / "verify.py"),
                  "--access", access])
    assert "native trigger/coverage PASS" in result.stdout
