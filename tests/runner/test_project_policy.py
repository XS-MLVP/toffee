"""Domain integration uses public hooks, not private collector replacement."""
import json
import pytest
from .test_execution import run_cli
from .test_native import NATIVE, TOOL


def test_synthetic_groups_do_not_contribute_to_measured_bins(tmp_path):
    output, _ = run_cli(tmp_path, '''
import pytest
from xreactor import Bin, CoverGroupDef, CoverPointDef
def group():
    return CoverGroupDef('events', (CoverPointDef('v', {'zero': Bin.values(0), 'one': Bin.values(1)}),)).instantiate('dut')
@pytest.fixture
def synthetic():
    g = group()
    g.sample({'v': 1})
    yield g
    g.sample({'v': 1})
def test_real():
    group().sample({'v': 0})
@pytest.mark.xreactor_functional_coverage(False)
def test_synthetic(synthetic):
    synthetic.sample({'v': 1})
''', workers=2, options=('--export-coverage-json',))
    group, = json.loads((output/'functional-coverage.json').read_text())['groups']
    assert group['samples'] == 1 and group['coverage'] == 50
    assert group['points'][0]['counts'] == {'zero': 1, 'one': 0}


def test_project_hook_receives_merged_memory_once_without_json(tmp_path):
    (tmp_path/'conftest.py').write_text('''
from pathlib import Path
def pytest_xreactor_coverage(config, functional, native, pytest_exitstatus):
    assert pytest_exitstatus == 0 and native == []
    assert functional['groups'][0]['samples'] == 4
    target = Path(config.getoption('xreactor_output'))/'project-summary.txt'
    with target.open('x') as stream: stream.write('checked once')
''')
    text = '''
import pytest
from xreactor import Bin, CoverGroupDef, CoverPointDef
@pytest.mark.parametrize('n', range(4))
def test_event(n):
    g = CoverGroupDef('events', (CoverPointDef('v', {'one': Bin.values(1)}),)).instantiate('dut')
    g.sample({'v': 1})
'''
    output, _ = run_cli(tmp_path, text, workers=2)
    assert (output/'project-summary.txt').read_text() == 'checked once'
    assert not (output/'functional-coverage.json').exists()
    assert json.loads((output/'manifest.json').read_text())['cleanup_complete']


def test_project_rejection_keeps_inputs_and_fails(tmp_path):
    (tmp_path/'conftest.py').write_text('''
def pytest_xreactor_coverage(config, functional, native, pytest_exitstatus):
    raise ValueError('rejected domain contract')
''')
    output, _ = run_cli(tmp_path, 'def test_ok(): pass\n', expected=1)
    manifest = json.loads((output/'manifest.json').read_text())
    assert manifest['state'] == 'incomplete' and manifest['inputs_retained']
    assert (output/'_workers').is_dir()


def test_auxiliary_group_is_not_a_second_measurement_or_schema_conflict(tmp_path):
    text = '''
from xreactor import Bin, CoverGroupDef, CoverPointDef
def test_groups():
    measured=CoverGroupDef('full', (CoverPointDef('v', {'zero': Bin.values(0), 'one': Bin.values(1)}),)).instantiate('dut')
    measured.sample({'v':0})
    helper=CoverGroupDef('local', (CoverPointDef('v', {'one': Bin.values(1)}),)).instantiate('dut', capture=False)
    helper.sample({'v':1})
    helper.assert_coverage(100)
'''
    output,_=run_cli(tmp_path,text,workers=2,options=('--export-coverage-json',))
    group,=json.loads((output/'functional-coverage.json').read_text())['groups']
    assert group['samples']==1 and group['coverage']==50
    assert group['schema']['name']=='full'


@pytest.mark.skipif(TOOL is None, reason='requires Verilator coverage processor')
def test_functional_exclusion_does_not_disable_native_dut_collection(tmp_path):
    (tmp_path/'test_user.sv').write_text('module DUT;\nwire toggle;\nendmodule\n')
    output, _ = run_cli(tmp_path, NATIVE+'\npytestmark = pytest.mark.xreactor_functional_coverage(False)\n',
                        workers=2, options=('--native-coverage-tool', TOOL))
    manifest = json.loads((output/'manifest.json').read_text())
    native, = [row for row in manifest['artifacts'] if row['kind']=='native']
    assert native['input_files']==40 and manifest['functional_groups']==0
    assert manifest['cleanup_complete'] and not (output/'_workers').exists()
