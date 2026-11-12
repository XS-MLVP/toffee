"""Opt-in end-to-end RTL regression; not a replacement for native unit tests."""

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples/integration/arrays"


@pytest.mark.parametrize("access", ("dpi", "mem_direct"))
def test_generated_picker_arrays(tmp_path, access):
    if os.environ.get("TOFFEE_ARRAY_RTL") != "1":
        pytest.skip("set TOFFEE_ARRAY_RTL=1 to build and run the Picker RTL array example")
    picker = os.environ.get("PICKER_BIN") or shutil.which("picker")
    template = os.environ.get("PICKER_TEMPLATE")
    assert picker, "TOFFEE_ARRAY_RTL requires PICKER_BIN or picker on PATH"
    assert shutil.which("verilator"), "TOFFEE_ARRAY_RTL requires Verilator"
    package = tmp_path / "UnpackedSignals"
    args = [picker, "export", str(EXAMPLE / "UnpackedSignals.sv"),
            "--sname", "UnpackedSignals", "--tdir", str(package),
            "--lang", "python", "--sim", "verilator", "--rw", access,
            "--autobuild", "false", "-V", "-Wno-fatal"]
    if template:
        args.extend(("--sdir", template))

    def run(command):
        result = subprocess.run(command, cwd=tmp_path, env=env,
                                text=True, capture_output=True, timeout=600)
        assert result.returncode == 0, result.stdout + result.stderr
        return result

    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join((str(tmp_path), str(ROOT / "src"), env.get("PYTHONPATH", "")))
    run(args)
    run(["make", "-C", str(package), "NPROC=4"])
    result = run([sys.executable, str(EXAMPLE / "verify.py"),
                  "--metadata", str(package / "signals.json"), "--access", access])
    assert "snapshots PASS" in result.stdout
