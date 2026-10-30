# Runner reference

`toffee run` executes pytest tests and collects and merges coverage. For installation and operation,
see [run tests and aggregate coverage](../guides/runner.md). For interpretation, see
[checking, coverage, and regression results](../explanation/coverage-results.md).

## Commands and options

```text
toffee run [Toffee 选项] [pytest 参数]
python3 -m toffee run [Toffee 选项] [pytest 参数]
```

The entry points are equivalent. Execution requires pytest; multiple workers also require pytest-xdist.

| Option | Default | Behavior |
| --- | --- | --- |
| `--workers N`, `-j N` | `1` | Positive integer; values above 1 use independent worker processes |
| `--output PATH` | `output/toffee-<UTC time>-<random ID>` | Results directory; must not exist |
| `--keep-coverage-inputs` | Off | Retain per-process intermediate inputs and raw exports after successful aggregation |
| `--export-coverage-json` | Off | Also save merged JSON when functional coverage exists |
| `--native-coverage-tool PATH` | Unspecified | Select the Verilator coverage processor explicitly; otherwise prefer the build provenance path, then PATH |

Test paths and options such as `-k`, `-m`, `-q`, and `--tb=short` pass through to pytest.
`--collect-only` lists tests without creating a measurement directory; an existing `--output`
path is still rejected. Use `--` to separate subsequent pytest arguments explicitly.

Set parallelism through `--workers`. Passing `-n`, `--numprocesses`, `--dist`, `--tx`, or
`--max-worker-restart` is prohibited. The Runner uses load scheduling and disables automatic worker
restart to identify incomplete regressions.

## Final artifacts

Paths below are relative to this run's `--output`. `manifest.json` records artifact inventory and identity.

| Path | Creation conditions and contents |
| --- | --- |
| `manifest.json` | Run state, test verdict, artifacts, and completeness; failure details depend on the failing phase |
| `coverage.html` | Aggregate report when functional or native coverage exists; absent without coverage |
| `functional-coverage.json` | Functional data exists and `--export-coverage-json` is enabled |
| `native/<build-id>/coverage.dat` | Complete native counts merged by DUT build identity |
| `native/<build-id>/coverage.info` | Line and branch coverage when the native tool supports LCOV export |
| `_workers/` | Intermediate inputs; removed after successful commit by default, retained on request or aggregation failure |

The default does not export JSON per test. Explicit `write_json()` calls are unaffected.
Native counts from different DUT builds never merge together; each build retains one final
`coverage.dat`. Merging retains zero-hit points, toggles, and other native counts, and verifies
that output counts equal the sum of inputs.

<a id="uncertain-outcome"></a>

## UNCERTAIN: insufficient evidence, pending conclusion

```python
import pytest

@pytest.mark.uncertain(reason="消费者合同尚未确认", reference="design-note/7")
def test_contract(dut):
    # 正常执行已有激励与检查，不跳过断言。
    assert dut.output == expected
```

- `reason` must be a non-empty string. `reference` is an optional non-empty
  string interpreted by the project. Toffee does not require bug IDs, residual
  registers, or a specific identifier format.
- When all phases complete and all assertions pass, the result is **UNCERTAIN**,
  not PASS. Progress shows a yellow `U`, verbose output shows `UNCERTAIN`, and
  the session summary reports count and reasons separately.
- Assertion failures remain FAIL, setup/teardown failures remain ERROR, and
  skipped cases remain SKIP. UNCERTAIN cannot hide real errors and cannot be
  combined with `xfail`.
- Works for function, class/module, and `pytest.param` markers; the closest
  marker wins. Counts, reasons, and references are consistent in single-process
  and xdist runs.
- `manifest.json.tests` includes the `UNCERTAIN` count, and
  `manifest.json.uncertain` stores `{nodeid, reason, reference?}` for final
  UNCERTAIN cases. Coverage still collects, merges, and commits normally.
- **UNCERTAIN alone does not change pytest exit status**: if results are only
  PASS/UNCERTAIN and there are no other errors, exit status is 0. Exit status 0
  means execution succeeded, not that all verification conclusions passed.

`toffee run` enables this automatically. When running pytest directly, load the
plugin explicitly:

```bash
python3 -m pytest -p toffee._pytest tests/
```

Project report adapters can reuse `toffee.outcomes.phase_record(report)`,
`report_status(report)`, and `case_status(phases)` and should avoid duplicating
UNCERTAIN rules. Underlying pytest phase `outcome` values remain standard
passed/failed/skipped; Toffee classification derives from marker metadata and
complete phase records. Third-party pytest reporters without this integration
may still show standard outcomes.

## States and exit behavior

| Situation | Result |
| --- | --- |
| Tests execute completely and all artifacts are written and validated | Manifest is `committed`; `pytest_exitstatus` and test counts still express the verdict |
| Assertion failure with complete execution and collection | Diagnostic reports can still commit; the command returns nonzero |
| PASS/UNCERTAIN only, with no other errors | Test counts separate the two; the command returns 0 and coverage can still commit |
| Interrupted collection, corrupt inputs, incomplete collection, or worker crash | Manifest is `incomplete`; available inputs remain and final artifacts may be partial |
| Artifacts committed, but intermediate cleanup fails | State remains `committed`, `cleanup_error` is recorded, and exit is nonzero |

The Runner normally returns pytest's exit code. Collection or cleanup failure changes an otherwise
successful run to nonzero. CLI argument errors return 2; a keyboard interrupt caught by the CLI
returns 130. Initialization failures may occur before a manifest exists; consult the exit status
and error output. Only this run's intermediate files are reclaimed, never historical run directories.

UNCERTAIN and `state="incomplete"` are different: UNCERTAIN means execution
completed but verification evidence is insufficient; `incomplete` means
execution or artifact collection did not finish. Crashes, missing phases, or
missing receipts cannot be reclassified as UNCERTAIN.

## Project extension interfaces

| Entry point | Contract |
| --- | --- |
| `@pytest.mark.toffee_functional_coverage(False)` | Exclude functional capture during this test and its fixture setup/teardown; native coverage stays enabled |
| `definition.instantiate("helper", capture=False)` | Exclude this helper from automatic aggregation; sampling, assertions, and manual export remain available |
| `toffee_register_dut` fixture | Callback registering DUTs that may close and be released during a test; registration neither advances nor closes them |
| `pytest_toffee_coverage(config, functional, native, pytest_exitstatus)` | Called once in the main process after merging and before commit; arguments are read-only; raising fails collection and preserves available inputs |

Automatically discovered DUTs must provide `SetCoverage`, `GetCovMetrics`, `GetXClock`, and `Finish`.
A fixture can return the DUT directly, an Agent with `dut`, or a factory retaining instances in
`_instances`. Fixtures still own initialization and `Finish()`. The Runner neither takes over
clock advancement nor closes Backends. A standalone clock does not identify its simulator and exports.
