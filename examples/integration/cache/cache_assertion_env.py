"""Native assertion runs retain exact diagnostics and RTL execution counts."""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys

from examples.integration.rtl_line_coverage import fields, RECORD


def build_fatal_coverage(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).resolve().parents[1] / "verilator_fatal_coverage.cpp"
    library = directory / "libtoffee_fatal_coverage.so"
    subprocess.run([*shlex.split(os.environ.get("CXX", "c++")), "-std=c++17", "-shared", "-fPIC",
                    "-O2", str(source), "-ldl", "-o", str(library)],
                   check=True, capture_output=True, text=True)
    return library


def install_fatal_coverage(dut, library, output):
    package = Path(sys.modules[type(dut).__module__].__file__).resolve().parent
    model = package / ("libUT" + type(dut).__name__.removeprefix("DUT") + ".so")
    hook = ctypes.CDLL(str(library))
    hook.toffee_configure_fatal_coverage.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
    hook.toffee_configure_fatal_coverage.restype = ctypes.c_int
    status = hook.toffee_configure_fatal_coverage(os.fsencode(model), os.fsencode(output))
    if status:
        raise RuntimeError(f"fatal coverage hook cannot use {model}: ABI status {status}")


def assertion_run(script, arguments, *, library, output, message, line, hierarchy):
    """Require the intended violation, fatal message, abort and nonzero counter."""
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    coverage = output / "verilator-coverage.dat"
    environment = os.environ.copy()
    environment["LD_PRELOAD"] = str(library) + (
        ":" + environment["LD_PRELOAD"] if environment.get("LD_PRELOAD") else "")
    result = subprocess.run([sys.executable, "-c", script, *arguments, str(library), str(coverage)],
                            env=environment, text=True, capture_output=True, timeout=20)
    log = result.stdout + result.stderr
    (output / "rtl-assertion.log").write_text(log)
    assert result.returncode == -signal.SIGABRT, f"expected SIGABRT from RTL assertion, got {result.returncode}:\n{log}"
    assert "Assertion failed" in log and message in log, log
    assert "toffee: RTL coverage saved before fatal" in log, log
    witness = next(json.loads(text.removeprefix("witness "))
                   for text in result.stdout.splitlines() if text.startswith("witness "))
    assert witness["violation"] is True, witness
    assert coverage.is_file(), log
    points = []
    for text in coverage.read_text().splitlines():
        match = RECORD.fullmatch(text)
        if match:
            point = fields(match.group(1))
            if (point.get("h") == hierarchy and point.get("page", "").startswith("v_branch/")
                    and int(point["l"]) == line and point.get("o") == "if"):
                points.append({**point, "count": int(match.group(2))})
    assert len(points) == 1 and points[0]["count"] > 0, (points, log)
    # Verify the original fatal's source too: another invariant can fail in
    # the same cycle, so a matching $fwrite alone is insufficient.
    source = Path(points[0]["f"])
    fatal_line = next(number for number, text in enumerate(source.read_text().splitlines(), 1)
                      if number >= line and "$fatal" in text)
    assert f"{source}:{fatal_line}: Verilog $stop" in log, log
    assert f"Assertion failed in {hierarchy}" in log, log
    evidence = {"returncode": result.returncode, "assertion": message,
                "fatal_source_line": fatal_line, "witness": witness,
                "coverage_point": points[0], "coverage_database": str(coverage)}
    (output / "assertion-witness.json").write_text(json.dumps(evidence, indent=2) + "\n")
    return evidence


def record_assertion_checks(evidence, scope, fault, output, *, existing=None):
    from examples.integration.cache.cache_internal_coverage import RULES
    from examples.integration.rtl_checks import ScenarioChecks

    name = "CACHE-INT-ERROR-ASSERTIONS"
    names = [name] + ([existing[0]] if existing else [])
    checks = ScenarioChecks({key: RULES[key] for key in names},
                            run_metadata={"scope": scope, "fault": fault}, contract="cache.rtl-properties/v1")
    checks.implemented = set(names)
    actual = {"violation": evidence["witness"]["violation"],
              "assertion_abort": evidence["returncode"] == -signal.SIGABRT,
              "counter_hit": evidence["coverage_point"]["count"] > 0}
    for requirement, scenario in [(name, f"{scope}.{fault}")] + ([existing] if existing else []):
        checks.check(requirement, scenario, actual=actual,
                     expected={"violation": True, "assertion_abort": True, "counter_hit": True},
                     tick=evidence["witness"]["tick"], context=evidence)
    checks.write(Path(output))
