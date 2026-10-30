"""UNCERTAIN is a runner result, not a swallowed assertion or a pytest skip."""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from toffee.outcomes import case_status
from .test_execution import SOURCE, run_cli


@pytest.mark.parametrize("workers", [1, 2])
def test_uncertain_runs_checks_is_yellow_and_keeps_exit_zero(tmp_path, workers):
    text = '''
import pytest
from toffee import Bin, CoverGroupDef, CoverPointDef
def test_pass():
    assert 2 + 2 == 4
@pytest.mark.uncertain(reason="other alignment histories are not yet checked")
@pytest.mark.parametrize('value', [1, 2])
def test_partial(value):
    group = CoverGroupDef('partial', (
        CoverPointDef('value', {'one': Bin.values(1), 'two': Bin.values(2)}),)
    ).instantiate('dut', run_id=str(value))
    group.sample({'value': value})
    assert value in (1, 2)
'''
    output, result = run_cli(tmp_path, text, workers=workers,
                             options=("--export-coverage-json", "--color=yes"))
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["state"] == "committed" and manifest["coverage_complete"]
    assert manifest["pytest_exitstatus"] == 0
    assert manifest["tests"] == {"PASS": 1, "UNCERTAIN": 2}
    assert manifest["uncertain"] == [
        {"nodeid": f"test_user.py::test_partial[{value}]",
         "reason": "other alignment histories are not yet checked"}
        for value in (1, 2)]
    assert "1 passed" in result.stdout and "2 uncertain" in result.stdout
    assert "3 passed" not in result.stdout
    assert "\x1b[33mU" in result.stdout, result.stdout
    assert "UNCERTAIN: evidence is not yet sufficient" in result.stdout
    group, = json.loads((output / "functional-coverage.json").read_text())["groups"]
    assert group["samples"] == 2  # The tests ran and contributed coverage.


@pytest.mark.parametrize("workers", [1, 2])
def test_uncertain_cannot_hide_a_failed_check_or_fixture(tmp_path, workers):
    text = '''
import pytest
@pytest.fixture
def broken_setup():
    raise RuntimeError("setup error")
@pytest.fixture
def broken_teardown():
    yield
    raise RuntimeError("teardown error")
@pytest.mark.uncertain(reason="oracle review still needed")
def test_failed_check():
    assert False, "real mismatch"
@pytest.mark.uncertain(reason="observation missing")
def test_setup_error(broken_setup):
    pass
@pytest.mark.uncertain(reason="only some checks available")
def test_teardown_error(broken_teardown):
    assert True
'''
    output, result = run_cli(tmp_path, text, workers=workers, expected=1)
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["tests"] == {"FAIL": 1, "ERROR": 2}
    assert manifest["state"] == "committed" and manifest["pytest_exitstatus"] == 1
    assert manifest["uncertain"] == []
    assert "1 failed" in result.stdout and "2 errors" in result.stdout
    assert "uncertain" not in result.stdout.rsplit("=", 2)[-2].lower(), result.stdout


@pytest.mark.parametrize("workers", [1, 2])
def test_uncertain_does_not_relabel_a_skip(tmp_path, workers):
    text = '''
import pytest
@pytest.mark.uncertain(reason="not all fields observed")
@pytest.mark.skip(reason="not available today")
def test_skip():
    raise AssertionError("must not run")
'''
    output, result = run_cli(tmp_path, text, workers=workers)
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["tests"] == {"SKIP": 1} and manifest["uncertain"] == []
    assert "1 skipped" in result.stdout


@pytest.mark.parametrize("marker", [
    "uncertain", "uncertain(reason='  ')", "uncertain('a reason')",
    "uncertain(reason=3)", "uncertain(reason='x', reference=1)",
    "uncertain(reason='x', reference='')", "uncertain(reason='x', residual='R-1')",
])
def test_invalid_declaration_is_a_collection_error(tmp_path, marker):
    text = f"import pytest\n@pytest.mark.{marker}\ndef test_bad(): pass\n"
    _, result = run_cli(tmp_path, text, expected=4)
    assert "uncertain" in result.stderr


def test_xfail_and_uncertain_cannot_be_combined(tmp_path):
    text = '''
import pytest
@pytest.mark.xfail(reason="known failure")
@pytest.mark.uncertain(reason="unconfirmed expectation")
def test_ambiguous():
    assert False
'''
    _, result = run_cli(tmp_path, text, expected=4)
    assert "uncertain and xfail cannot be combined" in result.stderr


def test_plain_pytest_plugin_has_uncertain_without_managed_output(tmp_path):
    test = tmp_path / "test_plain.py"
    test.write_text('''
import pytest
@pytest.mark.uncertain(reason="consumer contract not confirmed", reference="team/issue-7")
def test_contract():
    assert True
''')
    env = dict(os.environ, PYTHONPATH=str(SOURCE), PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    result = subprocess.run([sys.executable, "-m", "pytest", "-p", "toffee._pytest_plugin",
                             "-v", str(test)], cwd=tmp_path, env=env,
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "UNCERTAIN" in result.stdout and "1 uncertain" in result.stdout
    assert "[team/issue-7]" in result.stdout
    assert "1 passed" not in result.stdout
    assert not list(tmp_path.rglob("manifest.json"))


@pytest.mark.parametrize("workers", [1, 2])
def test_inherited_and_parameter_marks_keep_per_case_reasons(tmp_path, workers):
    text = '''
import pytest
@pytest.mark.uncertain(reason="class-level question")
class TestContracts:
    def test_inherited(self): pass
    @pytest.mark.uncertain(reason="narrower question", reference="design-note")
    def test_override(self): pass
@pytest.mark.parametrize('value', [
    0, pytest.param(1, marks=pytest.mark.uncertain(reason="parameter-specific"))])
def test_parameter(value): assert value in (0, 1)
'''
    output, _ = run_cli(tmp_path, text, workers=workers)
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["tests"] == {"PASS": 1, "UNCERTAIN": 3}
    assert [row["reason"] for row in manifest["uncertain"]] == [
        "class-level question", "narrower question", "parameter-specific"]
    assert manifest["uncertain"][1]["reference"] == "design-note"


def test_case_status_never_calls_an_interrupted_case_uncertain():
    phases = {"setup": {"outcome": "passed"},
              "call": {"outcome": "passed", "uncertain": {"reason": "partial"}}}
    assert case_status(phases) == "INCOMPLETE"
    phases["teardown"] = {"outcome": "passed"}
    assert case_status(phases) == "UNCERTAIN"
    phases["call"]["outcome"] = "failed"
    assert case_status(phases) == "FAIL"
    phases["teardown"]["outcome"] = "failed"
    assert case_status(phases) == "ERROR"


@pytest.mark.parametrize("strict", [False, True])
def test_xfail_and_xpass_classification_is_unchanged(tmp_path, strict):
    text = f'''
import pytest
@pytest.mark.xfail(strict={strict!r}, reason="known")
def test_known(): assert False
@pytest.mark.xfail(strict={strict!r}, reason="obsolete")
def test_fixed(): pass
'''
    output, _ = run_cli(tmp_path, text, expected=int(strict))
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["tests"] == {"XFAIL": 1, "XPASS": 1}
    assert manifest["uncertain"] == []
