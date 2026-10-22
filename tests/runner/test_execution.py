"""Black-box runner acceptance through the actual subprocess/xdist lifecycle."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "src"


def run_cli(tmp_path, text, *, workers=1, options=(), expected=0, output_name="artifacts"):
    test = tmp_path / "test_user.py"
    test.write_text(text)
    output = tmp_path / output_name
    env = dict(os.environ, PYTHONPATH=str(SOURCE), PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    result = subprocess.run([sys.executable, "-m", "toffee", "run", str(test),
        "--workers", str(workers), "--output", str(output), "-q", *options],
        cwd=tmp_path, env=env, text=True, capture_output=True, timeout=60)
    assert result.returncode == expected, result.stdout + result.stderr
    return output, result


SAMPLE = '''
import pytest
from toffee import Bin, CoverGroupDef, CoverPointDef, CrossDef
definition = CoverGroupDef('events', (
    CoverPointDef('a', {'zero': Bin.values(0), 'one': Bin.values(1)}),
    CoverPointDef('b', {'zero': Bin.values(0), 'one': Bin.values(1)})),
    (CrossDef('ab', ('a', 'b')),))
@pytest.mark.parametrize('value', range(12))
def test_hits(value):
    group = definition.instantiate('dut', run_id=f'case-{value}')
    group.sample({'a': value % 2, 'b': 0}, details=False)
'''


def test_parallel_counts_and_provenance_match_serial_without_case_exports(tmp_path):
    results = []
    for workers in (1, 3):
        directory = tmp_path / str(workers)
        directory.mkdir()
        output, _ = run_cli(directory, SAMPLE, workers=workers, options=("--export-coverage-json",))
        manifest = json.loads((output / "manifest.json").read_text())
        assert manifest["state"] == "committed" and manifest["cleanup_complete"]
        assert manifest["tests"] == {"PASS": 12}
        assert len(manifest["workers"]) == workers
        assert not (output / "_workers").exists()
        assert sorted(p.name for p in output.iterdir()) == ["coverage.html", "functional-coverage.json", "manifest.json"]
        group, = json.loads((output / "functional-coverage.json").read_text())["groups"]
        assert group["samples"] == 12
        assert len(group["origins"]) == 12
        assert len(group["crosses"][0]["counts"]) == 4
        assert sum(n == 0 for n in group["crosses"][0]["counts"].values()) == 2
        results.append(group)
    for key in ("schema", "instance", "samples", "gated", "coverage"):
        assert results[0][key] == results[1][key]
    for category in ("points", "crosses"):
        assert [p["counts"] for p in results[0][category]] == [p["counts"] for p in results[1][category]]
        for group in results:
            for point in group[category]:
                for name, evidence in point["provenance"].items():
                    assert sum(row["count"] for row in evidence.values()) == point["counts"][name]


@pytest.mark.parametrize("workers", [1, 3])
def test_cross_does_not_invent_combinations_and_uses_merged_hit_threshold(tmp_path, workers):
    text = '''
import pytest
from toffee import Bin, CoverGroupDef, CoverPointDef, CrossDef
definition = CoverGroupDef('cross-events', (
    CoverPointDef('a', {'zero': Bin.values(0), 'one': Bin.values(1)}),
    CoverPointDef('b', {'zero': Bin.values(0), 'one': Bin.values(1)})),
    (CrossDef('ab', ('a', 'b'), at_least=2),))
@pytest.mark.parametrize('value', range(4))
def test_hits(value):
    group = definition.instantiate('dut')
    group.sample({'a': value % 2, 'b': 1 - value % 2})
    assert group.cross_coverage('ab') == 0
'''
    output, _ = run_cli(tmp_path, text, workers=workers, options=("--export-coverage-json",))
    group, = json.loads((output / "functional-coverage.json").read_text())["groups"]
    cross, = group["crosses"]
    counts = {tuple(json.loads(key)): count for key, count in cross["counts"].items()}
    assert counts == {("zero", "zero"): 0, ("zero", "one"): 2,
                      ("one", "zero"): 2, ("one", "one"): 0}
    assert cross["coverage"] == 50
    assert len(group["origins"]) == 4


@pytest.mark.parametrize("workers", [1, 2])
def test_default_has_no_functional_json_or_worker_inputs(tmp_path, workers):
    output, _ = run_cli(tmp_path, SAMPLE, workers=workers)
    assert sorted(p.name for p in output.iterdir()) == ["coverage.html", "manifest.json"]
    assert not list(output.rglob("functional*.json*"))


def test_keep_inputs_is_explicit_and_preserves_debug_snapshots(tmp_path):
    output, _ = run_cli(tmp_path, SAMPLE, workers=2, options=("--keep-coverage-inputs",))
    assert len(list(output.glob("_workers/*/cases/*.json"))) == 12
    assert len(list(output.glob("_workers/*/functional.json.gz"))) == 2
    assert json.loads((output / "manifest.json").read_text())["cleanup_complete"]


def test_failure_is_preserved_as_failure_with_diagnostic_coverage(tmp_path):
    output, _ = run_cli(tmp_path, SAMPLE + '\ndef test_wrong():\n    assert False, "original mismatch"\n',
        workers=2, expected=1)
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["pytest_exitstatus"] == 1
    assert manifest["tests"] == {"FAIL": 1, "PASS": 12}
    assert manifest["coverage_complete"] and manifest["cleanup_complete"]
    assert "test exit status 1" in (output / "coverage.html").read_text()


def test_session_and_module_groups_are_collected_once_after_final_teardown(tmp_path):
    text = '''
import pytest
from toffee import Bin, CoverGroupDef, CoverPointDef
@pytest.fixture(scope='session')
def group():
    yield CoverGroupDef('session', (CoverPointDef('a', {'one': Bin.values(1)}),)).instantiate('session')
@pytest.mark.parametrize('n', range(5))
def test_group(group, n):
    group.sample({'a': 1})
'''
    output, _ = run_cli(tmp_path, text, workers=2, options=("--export-coverage-json",))
    group, = json.loads((output / "functional-coverage.json").read_text())["groups"]
    assert group["samples"] == 5
    assert len(group["origins"]) == 2


def test_worker_crash_keeps_uncommitted_inputs_and_fails(tmp_path):
    text = SAMPLE + '\ndef test_crash():\n    import os\n    os._exit(19)\n'
    output, _ = run_cli(tmp_path, text, workers=2, expected=1)
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["state"] == "incomplete"
    assert manifest["inputs_retained"]
    assert (output / "_workers").is_dir()
    assert not (output / "coverage.html").exists()


def test_collection_only_creates_no_measurement_directory(tmp_path):
    output, _ = run_cli(tmp_path, SAMPLE, workers=2, options=("--collect-only",))
    assert not output.exists()


def test_incompatible_schema_rejected_and_inputs_retained(tmp_path):
    text = '''
import pytest
from toffee import Bin, CoverGroupDef, CoverPointDef
@pytest.mark.parametrize('n', [0, 1])
def test_changed(n):
    g = CoverGroupDef('changed', (CoverPointDef('a', {str(n): Bin.values(n)}),)).instantiate('same')
    g.sample({'a': n})
'''
    output, _ = run_cli(tmp_path, text, workers=2, expected=1)
    assert json.loads((output / "manifest.json").read_text())["state"] == "incomplete"
    assert list(output.glob("_workers/*/functional.json.gz"))


def test_existing_output_is_never_overwritten(tmp_path):
    output, _ = run_cli(tmp_path, SAMPLE)
    original = (output / "manifest.json").read_bytes()
    run_cli(tmp_path, SAMPLE, expected=2)
    assert (output / "manifest.json").read_bytes() == original


def test_plain_pytest_does_not_enable_automatic_export(tmp_path):
    test = tmp_path / "test_plain.py"
    test.write_text(SAMPLE)
    env = dict(os.environ, PYTHONPATH=str(SOURCE), PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", str(test)], cwd=tmp_path,
        env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not list(tmp_path.glob("**/manifest.json"))


def test_async_fixture_groups_are_captured_without_explicit_export(tmp_path):
    text = '''
import pytest
import pytest_asyncio
from toffee import Bin, CoverGroupDef, CoverPointDef
@pytest_asyncio.fixture
async def group():
    yield CoverGroupDef('async', (CoverPointDef('a', {'one': Bin.values(1)}),)).instantiate('dut')
@pytest.mark.asyncio
async def test_async(group):
    group.sample({'a': 1})
'''
    output, _ = run_cli(tmp_path, text, workers=2, options=("-p", "asyncio", "--export-coverage-json"))
    group, = json.loads((output / "functional-coverage.json").read_text())["groups"]
    assert group["samples"] == 1


@pytest.mark.parametrize('workers', [1, 2])
def test_collection_error_never_becomes_committed_empty_coverage(tmp_path, workers):
    output, _ = run_cli(tmp_path, 'def test_broken(:\n    pass\n', workers=workers,
                        expected=2 if workers == 1 else 1)
    assert json.loads((output / 'manifest.json').read_text())['state'] == 'incomplete'


def test_corrupt_worker_checkpoint_is_rejected(tmp_path):
    text = SAMPLE + '''
def test_corrupt():
    import toffee._pytest as plugin
    from pathlib import Path
    original = plugin._WorkerCoverage.finish
    def finish(self, status):
        receipt = original(self, status)
        Path(receipt['functional']['path']).write_bytes(b'corrupt')
        return receipt
    plugin._WorkerCoverage.finish = finish
'''
    output, _ = run_cli(tmp_path, text, expected=1)
    assert json.loads((output / 'manifest.json').read_text())['state'] == 'incomplete'
    assert list(output.glob('_workers/*/functional.json.gz'))


def test_report_write_failure_keeps_inputs_uncommitted(tmp_path):
    text = SAMPLE + '''
def test_break_renderer():
    import toffee._pytest as plugin
    def broken(*args, **kwargs):
        raise OSError('injected publication failure')
    plugin.render_unified_coverage_html = broken
'''
    output, _ = run_cli(tmp_path, text, expected=1)
    manifest = json.loads((output / 'manifest.json').read_text())
    assert manifest['state'] == 'incomplete' and manifest['inputs_retained']
    assert list(output.glob('_workers/*/functional.json.gz'))


def test_cleanup_failure_preserves_commit_and_can_recover_without_remerging(tmp_path, monkeypatch):
    text = SAMPLE + '''
def test_break_cleanup():
    import toffee._pytest as plugin
    def broken(*args, **kwargs):
        raise OSError('injected cleanup failure')
    plugin.shutil.rmtree = broken
'''
    output, _ = run_cli(tmp_path, text, expected=1)
    manifest = json.loads((output / 'manifest.json').read_text())
    assert manifest['state'] == 'committed' and not manifest['cleanup_complete']
    assert 'injected cleanup failure' in manifest['cleanup_error']
    assert (output / '_workers').exists()
    from types import SimpleNamespace
    import toffee._pytest as plugin
    owner = plugin._ExecutionCoverage.__new__(plugin._ExecutionCoverage)
    owner.root, owner.keep = output, False
    def no_merge():
        raise AssertionError('cleanup recovery attempted another merge')
    monkeypatch.setattr(plugin, 'CoverageDatabase', no_merge)
    owner._finalize(SimpleNamespace(exitstatus=0))
    final = json.loads((output / 'manifest.json').read_text())
    assert final['state'] == 'committed' and final['cleanup_complete']
    assert final['artifacts'] == manifest['artifacts']
    assert not (output / '_workers').exists()


def test_preconfigured_conftest_group_is_captured_without_registration(tmp_path):
    (tmp_path / 'conftest.py').write_text('''
import pytest
from toffee import Bin, CoverGroupDef, CoverPointDef
group = CoverGroupDef('global', (CoverPointDef('a', {'one': Bin.values(1)}),)).instantiate('dut')
@pytest.fixture(scope='session')
def shared(): return group
''')
    text = '''
import pytest
@pytest.mark.parametrize('n', range(3))
def test_shared(shared, n): shared.sample({'a': 1})
'''
    output, _ = run_cli(tmp_path, text, workers=2, options=('--export-coverage-json',))
    group, = json.loads((output/'functional-coverage.json').read_text())['groups']
    assert group['samples'] == 3


@pytest.mark.parametrize('argument', ['-n2', '--numprocesses=2', '--dist=each', '--max-worker-restart=1'])
def test_worker_process_options_must_go_through_cli(tmp_path, argument):
    output, _ = run_cli(tmp_path, SAMPLE, options=(argument,), expected=2)
    assert not output.exists()


@pytest.mark.parametrize('workers', [1, 2])
def test_xfail_skip_and_strict_xpass_verdicts_are_preserved(tmp_path, workers):
    text = '''
import pytest
@pytest.mark.xfail(strict=True)
def test_known(): assert False
@pytest.mark.xfail(strict=True)
def test_fixed(): pass
@pytest.mark.skip(reason='explicit selection')
def test_skip(): pass
'''
    output, _ = run_cli(tmp_path, text, workers=workers, expected=1)
    manifest = json.loads((output/'manifest.json').read_text())
    assert manifest['state'] == 'committed'
    assert manifest['pytest_exitstatus'] == 1
    assert manifest['tests'] == {'XFAIL': 1, 'XPASS': 1, 'SKIP': 1}
