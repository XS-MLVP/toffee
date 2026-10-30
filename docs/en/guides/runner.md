# Run tests and aggregate coverage

This guide runs a project regression: select tests, collect coverage, read results, and investigate
collection failures. Tests should already run independently under pytest. Install runner dependencies
in the source checkout first:

```bash
python3 -m pip install -e '.[runner]'
toffee run tests/ --workers 8 --output output/regression-001
```

Replace `tests/` with your project's test directory and choose a new `--output` directory.
The terminal prints the artifact directory at completion. When coverage exists, open `coverage.html`
and also check pytest's result and the command exit code. See [Runner reference](../reference/runner.md)
for all options and file definitions.

## Select tests and worker count

```bash
# 只运行名称中含有 prediction 的测试。
toffee run tests/ --workers 8 --output output/prediction-001 -k prediction

# 单进程诊断，仍然使用相同的收集和报告流程。
toffee run tests/ --workers 1 --output output/serial-001
```

Options such as `-k`, `-m`, `-q`, and `--tb=short` pass through to pytest. `--collect-only` lists tests
without creating a measurement directory. The `--output` path must not already exist, keeping runs separate.

## Run a case whose evidence is not yet sufficient

Declare `@pytest.mark.uncertain(reason="...")`. Existing stimulus and assertions still run.
Only a case that would otherwise succeed becomes UNCERTAIN; real errors still fail. Progress
shows `U`, the final summary separates counts such as `1 passed, 2 uncertain`, and reasons are
listed individually. Add an optional `reference="..."` for a project entry; Toffee imposes no format.

UNCERTAIN does not count as PASS, but by default it neither changes the exit code nor prevents
coverage commit. An exit code of zero cannot replace reading the summary. See
[Runner reference](../reference/runner.md#uncertain-outcome) for declaration rules, manifest fields,
and explicit plugin loading with direct pytest.

## Collect functional coverage

Create covergroups following the [coverage guide](coverage.md), and sample observed events.
Tests do not need to create a collector: the Runner captures and merges data automatically.
To save the final functional coverage database, add:

```bash
toffee run tests/ --workers 8 --output output/export-001 --export-coverage-json
```

Existing calls to `write_json()` still write their files. After adopting automatic collection,
remove manual exports used solely for cross-case aggregation to avoid redundant copies.

### Exclude model self-tests from DUT coverage

Coverage-model unit tests may fill bins artificially to test classification. Mark them explicitly:

```python
import pytest


@pytest.mark.toffee_functional_coverage(False)
def test_coverage_definition():
    # 在这里测试分桶或合并规则，不计入正式功能覆盖率。
    ...
```

The marker covers fixture setup, the test body, and teardown, without disabling native DUT coverage.
Do not put artificial samples and real DUT samples into one session-scoped shared group.

For a helper group used only by local assertions, call
`definition.instantiate("helper", capture=False)`. It still supports sampling, checking, and manual
export, but does not enter automatic aggregation.

### Add a project check after merging

Implement `pytest_toffee_coverage` in a project plugin to check aggregate coverage goals. Read the
merged results in the hook and raise on failure; the Runner retains available inputs and reports
failure. See [Runner reference](../reference/runner.md) for its signature and timing. Ordinary
regressions do not require this hook.

## Collect native DUT coverage

Let fixtures initialize and call `Finish()` on Picker/Verilator DUTs. Return the DUT, an Agent with
a `dut` attribute, or a factory retaining objects in `_instances`. Ensure the generated package
provides the [discovery interfaces](../reference/runner.md).

If a factory closes and releases DUTs during the test, register them at creation through the
`toffee_register_dut` fixture callback so teardown can still locate them. A standalone clock
object cannot locate DUT export files.

Native counts merge by DUT build identity. To select a particular coverage processor:

```bash
toffee run tests/ --output output/native-001 \
  --native-coverage-tool /path/to/verilator_coverage
```

The Runner does not drive simulation clocks or close Backends. Retain project fixture cleanup.

## Locate results and diagnose failures

Start with pytest's errors, then inspect this run's `manifest.json`.

| Symptom | Next step |
| --- | --- |
| Output directory already exists | Choose a new directory; preserve the earlier evidence |
| Manifest is `committed`, but tests FAIL | Follow assertion or Scoreboard diagnostics; use coverage to locate scenarios |
| Manifest is `incomplete` | Read `errors` and worker logs; check crashes, incomplete collection, or incompatible models; preserve inputs |
| `cleanup_error` exists | Check permissions and open files; committed final artifacts remain valid |
| Tests pass without `coverage.html` | Check that groups were created and sampled, or that native DUT discovery succeeded |

To retain successful runs' per-process intermediate data for comparison, add `--keep-coverage-inputs`
to a new run. Aggregation failures already preserve available inputs without requiring a rerun.
See [Runner reference](../reference/runner.md) for file and state definitions.

Report completeness and DUT checking success are separate questions; see
[checking, coverage, and regression results](../explanation/coverage-results.md).
