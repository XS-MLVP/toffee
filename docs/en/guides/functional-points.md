# Functional requirements, scenarios, and coverage across tests

Requirements define functional points; test cases provide stimulus. Their relationship is many-to-many:
several cases/seeds can cover one point, and one case can contribute to many points. Toffee does
not require functional labels on tests. Actual observations in the environment produce coverage;
pytest executes tests and records pass/fail results.

## Turn requirements into observable scenarios

Define the required behavior, triggering conditions, observation location, and checking evidence
before designing stimulus. For a cache refill:

| Requirement | Scenario that must occur | Checking evidence |
| --- | --- | --- |
| Start filling at the requested word | All eight starting words | Accepted request address and first array-write address |
| Advance only on acceptance | Acceptance, response bubbles, 7→0 wraparound | Acceptance ledger, counter before/after, and write address |
| Commit metadata only on the final beat | Intermediate beats, bubble before final beat, final acceptance | Metadata write enable and new tag/valid |

Represent scenarios as CoverPoint bins, crosses, or timing events identified by project observers.
Hits must come from actual transactions or states, not case names, random constraints, or simply
setting a stall parameter. Distinguish data sources. Use an independent model or event ledger
for checking instead of copying RTL equations as the sole oracle.

A bin hit records that a condition occurred; assertions and Scoreboards check the resulting
behavior. An untriggered condition with no error is not completed verification. A triggered
condition with a checking failure cannot be dismissed because coverage met its target. State
whether sampling happens at input acceptance, DUT output observation, or successful comparison.

## Centralize sampling in the environment

Attach collectors to the environment or Agent observation path so applicable cases share them.
Cases configure and generate stimulus without listing functional points they own. Project cycle
observers can sample internal mechanisms; generic Drivers and scheduling need not know protocols.

The [component and artifact recipe](verification-flow.md) samples when the Monitor delivers a
record and passes that same observation into the Scoreboard, avoiding competing consumers.
Observations already made remain in that run's coverage file if checking fails; failure status
is retained separately.

A timing scenario must be observed completely within one run. Observing request A in one run and
request B in another does not demonstrate simultaneous contention. Cross merging combines actual
observed tuples; it does not form a Cartesian product of individual hits from different runs.

## Let several cases contribute

The runnable [test_pipeline_coverage.py example](https://github.com/XS-MLVP/toffee/blob/main/examples/transactions/test_pipeline_coverage.py)
uses one environment and the same `pipeline.output` instance name:

| Case | Observed response tags | Coverage |
| --- | --- | --- |
| first, seed 17 | 1, 2 | 2/3 |
| second, seed 23 | 2, 3 | 2/3 |
| Combined runs | 1, 2, 3 | 3/3 |

```bash
python3 -m pytest -q -s examples/transactions/test_pipeline_coverage.py
```

Both cases should pass; neither must reach global 100% alone. Each prints its artifact directory.
Give both `functional.json` files to one reporting command, substituting actual directories for
run-a/run-b:

```bash
toffee-coverage-report \
  --functional run-a/functional.json \
  --functional run-b/functional.json \
  --output output/coverage.html --site output/coverage-site
```

Tag 2 has two merged hits but still contributes one bin. With default `at_least=1`, coverage is
the union of hit sets. With a higher target, sum counts first, then check the target. Do not
average case percentages or use the number of tests as the denominator.

Check the aggregate goal from Python:

```python
from toffee import CoverageDatabase

combined = CoverageDatabase.read_json("run-a/functional.json")
combined.merge(CoverageDatabase.read_json("run-b/functional.json"))
combined["pipeline.output"].assert_coverage(100.0)
combined.write_json("output/combined.json")
```

Use a stable instance name for the same DUT location across cases; do not append case names or
seeds. Distinct DUT instances retain separate results; type aggregation requires `type_reports()`.
Incompatible schemas for one instance raise an error. The project decides whether RTL versions,
configurations, and input files belong in one regression; these identities are not automatically
validated. Duplicate or overlapping collection origins are rejected so copied files and cumulative
snapshots cannot inflate counts. Directories, seeds, node IDs, and run status locate evidence;
they do not determine bin hits.

The recipe records independent identity, test name/seed, and sampling location through instantiate's
`run_id`, `run_metadata`, and contract. Each ordinary merged bin retains origin counts and first/last
metadata, showing which runs contributed. Native counts without actual hit locations do not invent
timestamps. A seed alone is not a run identity, and putting only run_id in sample metadata does not
replace database identity. See [coverage](coverage.md) for formats and acceptance rules.

## Adjust tests using aggregate gaps

| Finding | Next step |
| --- | --- |
| Stimulus exists, but no matching collector | Add observation and measure; do not report 0% |
| A legal scenario has a collector but no hits | Adjust constraints or add a directed sequence; check reachability |
| Serial stimulus cannot create contention | Add overlapping inputs, resource contention, or module-level scenarios |
| Scenario hit but checking failed | Retain both coverage and failure evidence; investigate |
| Test skipped or not run | Record this in pytest; keep other runs' contributions |

Apply acceptance goals to the merged results of the selected regression. A directed case may
also check that its intended scenario occurred; this checks stimulus effectiveness without
requiring every case to complete the entire coverage model.

## What belongs in a project plan

The [e203](https://github.com/XS-MLVP/toffee/blob/main/examples/integration/e203/verification_plan.json)
and [Cache](https://github.com/XS-MLVP/toffee/blob/main/examples/integration/cache/verification_plan.json)
plans keep `scope` and `requirements`: design scope, source baseline, stable IDs, required behavior,
observation locations, scenarios, and checking evidence. They support design review; they are not
pytest input and do not compute completion from test labels. Existing checker locations are reading
aids, not coverage ownership rules.

Internal obligations include state transitions, register load/hold behavior, forwarding, arbitration,
counters, commit, and retirement. e203's 49 and Cache's 77 obligations are connected to project
property observers. Runs record actual activation in `internal-functional-coverage.json`, with
comparison and closure state separately in `internal-checks.json`. Unobserved scenarios remain
unmeasured, and hits do not replace property checking. Reports and evidence remain local, outside
Git. The plans and project checker code define their precise contracts.

See [coverage](coverage.md) for bins, gates, crosses, and reporting APIs.
