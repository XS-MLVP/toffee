# Running tests with automatic coverage

Install the execution dependencies and use XReactor's command-line entry point:

```bash
python -m pip install 'xreactor[runner]'
xreactor run tests/ --workers 8
```

`python -m xreactor run` is equivalent. XReactor loads its internal integration;
pytest executes the tests and fixtures, and pytest-xdist schedules independent
worker processes. Tests do not create run managers, worker collectors or merge
callbacks. Ordinary `pytest` and library imports keep their existing behavior.

Choose a new output directory with `--output`. Existing directories are rejected
to prevent mixing measurements. Arguments such as `-k`, `-m`, `-q` and `--tb=short`
are forwarded to pytest. `--workers 1` uses the same collection and publication
path without xdist. `--collect-only` produces no measurement directory.

```bash
xreactor run tests/ --workers 8 --output output/regression-001 -k prediction
xreactor run tests/ --workers 1 --keep-coverage-inputs
```

## Functional coverage

Create and sample the existing covergroups normally. Groups created by a test
are accumulated into its worker's database after teardown. Groups created in
module/session fixtures or during collection are captured once at worker
shutdown. Counters, zero-count bins, instance identities, sampling contracts
and origin provenance use the existing `CoverageDatabase.merge()` semantics.
Reconstructed report objects are not registered as new measurements.

The default path writes no per-case coverage JSON. At worker shutdown a single
compressed checkpoint transfers the database to the controller. The controller
merges all workers, renders the self-contained HTML directly from memory, commits
the final manifest and reclaims the checkpoints. Small manifest metadata remains.
`--export-coverage-json` additionally retains one final functional database.

Explicit calls to `write_json()` still write exactly the requested file. Remove
legacy per-case export calls when adopting automatic collection; otherwise those
explicit exports remain additional user-requested artifacts. Objects declared in
function scope should have that lifetime; use a module/session fixture for a
group intentionally shared across cases.

## Native DUT coverage

The built-in Picker/Verilator adapter discovers fixture-owned DUT objects exposing
`SetCoverage`, `GetCovMetrics`, `GetXClock` and `Finish`. A fixture may return the
DUT, an agent exposing `dut`, or a factory retaining `_instances`. Discovery runs
after fixture setup and before teardown, so factories may construct instances
in the test body. The fixture continues to own DUT initialization and `Finish()`.
The runner does not take ownership of the simulation backend or close it.

Coverage-enabled DUTs receive private export paths. Their complete native tables
are merged in bounded batches inside each worker and again across workers. Each
distinct DUT build retains one `native/<build-id>/coverage.dat`, independent of
worker count. Build identities include the generated module, nearby RTL/native
libraries and build provenance when available. Different builds remain separate.
The complete point universe, including toggle counters and zero counts, is
validated and retained. Every merge also checks each output counter against the
exact sum of its inputs before replacing the previous aggregate or deleting
inputs. Invalid, overflowing or altered counters fail the run and retain the
available inputs. Derivative LCOV reports select line/branch records.

The adapter uses `coverage_processor` from adjacent `build-provenance.json`,
falling back to `verilator_coverage` on PATH. Override it with
`--native-coverage-tool /path/to/verilator_coverage`.

Only fixture-owned discoverable DUTs are managed. A standalone clock does not
identify its simulator or coverage export owner. Other simulator integrations
can use the same internal export/merge boundary; no simulator is inferred from
an arbitrary clock object or from scanning unrelated directories.

## Retention and failures

By default a completed run retains `coverage.html`, a small `manifest.json`,
and any final DAT/LCOV artifacts. `--keep-coverage-inputs` also retains worker
checkpoints, per-case functional snapshots and original native dumps for debugging.

The manifest is the commit boundary: all final artifacts are written and hashed
before it changes to `committed`. Only then are exclusively owned worker inputs
removed. An interrupted merge, corrupt receipt, changed input, collection error
or crashed worker leaves the run `incomplete` and retains available inputs.
A cleanup failure preserves the committed result and records `cleanup_error`;
it does not rerun an additive merge.

Test failures remain failures and retain pytest's nonzero exit status. A completed
failed test execution can still produce diagnostic coverage; HTML includes the
test exit status and the manifest keeps test verdict counts separately. Coverage
collection completion does not assert that the design passed verification.

Historical run directories are not deleted automatically. Input consumption
only concerns the current, exclusively owned execution directory.
