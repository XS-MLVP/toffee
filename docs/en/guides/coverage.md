# Functional coverage and reports

Functional coverage answers which situations have been observed and which are missing.
It does not establish design correctness. Start with situations required by the specification,
define bins, and sample when those events actually occur.

Sample transactions captured by a Monitor manually, or bind signals and a clock for automatic
collection under Execution. This page uses direct definitions. For larger reusable models, see
[class declarations](coverage-classes.md); for events across cycles, see [temporal coverage](coverage-patterns.md);
for latency associated with request IDs, see [transaction coverage](coverage-transactions.md).

## Define a covergroup

```python
from toffee import Bin, CoverGroupDef, CoverPointDef, CrossDef

definition = CoverGroupDef(
    "fetch",
    (
        CoverPointDef(
            "target",
            {
                "local": Bin.values("local"),
                "external": Bin.values("external"),
            },
            description="Exercises both fetch routes.",
        ),
        CoverPointDef(
            "response",
            {
                "ok": Bin.values("ok"),
                "error": Bin.values("error"),
            },
            description="Exercises success and error responses.",
        ),
    ),
    (
        CrossDef(
            "target_x_response",
            ("target", "response"),
            description="Checks responses on every route.",
        ),
    ),
    description="End-to-end fetch coverage.",
)

coverage = definition.instantiate("core0.ifu")
coverage.sample(
    {"target": "local", "response": "ok"},
    metadata={"seed": 7, "tick": 42},
    details=False,
)
coverage.sample({"target": "local", "response": "error"})
coverage.sample({"target": "external", "response": "ok"})
coverage.sample({"target": "external", "response": "error"})
coverage.assert_coverage(100.0)
```

`details=False` accumulates counts and run evidence without returning per-sample bin-hit details.

The environment can provide run identity, context, and transaction sampling rules without
requiring functional labels on each test case:

```python
coverage = definition.instantiate(
    "core0.ifu", run_id="ci-job-42/shard-1/run-7",
    run_metadata={"test": "test_fetch", "seed": 7, "rtl_build": "build-123"},
    contract="ifu.response-observed-before-scoreboard/v1",
)
```

Omitting `run_id` generates a unique identity. An explicit ID must identify an independent
collection, not merely a seed: the same seed can run repeatedly. `run_metadata` stores project
context but does not validate RTL/config compatibility. To prevent merging incompatible sampling
semantics, include their stable name and version in the contract.

Ordinary point/cross hits store each origin's count and first/last metadata in
`provenance[bin_id][origin_id]`, not the complete transaction stream. `origins` associates run IDs,
instances, and run metadata. Manual sampling and Python collectors can retain actual hit locations.
Native counters currently retain run association and counts only: first/last metadata is null,
and synchronization time is never substituted for hit time. Hits without metadata also use null.

## Bin types

```python
Bin.values(0, 1)
Bin.transition(0, 1, 2)
Bin.range(0, 15)
Bin.ranges((0, 3), (8, 11))
Bin.masked(value=0b1000, mask=0b1100, width=4)
Bin.array("bucket", 0, 255, count=16)
Bin.default()
Bin.ignore(Bin.values(7))
Bin.illegal(Bin.range(12, 15))
```

`at_least=N` requires N hits. `ignore` bins do not contribute to the coverage denominator.
`illegal` raises `IllegalBinError` immediately by default; an instance can instead select the
record policy. Descriptions appear in JSON and HTML without entering the sampling hot path.

## Adjacent transitions

`Bin.transition(0, 1, 2)` requires three consecutive samples with those values. For manual
`sample()`, adjacency means consecutive calls; with a clock binding, it means consecutive selected
sampling phases. An intervening state prevents the match.

Sliding overlap is enabled by default: three samples of 1 hit `Bin.transition(1, 1)` twice.
With `overlap=False`, matching restarts at the sample after completion, giving one hit.
`at_least` always describes a cumulative hit target. A three-sample hold can be expressed as
`Bin.transition(1, 1, 1)`.

A disabled gate or X/Z in a required field breaks that point's transition history. Ignore bins
change classification, not time: `1, 9, 2` cannot hit `1→2` even if 9 is ignored. Unknown data
appears in the point's `unknown` statistics.

## Bind signals for Execution to manage

```python
from toffee import Execution, RisingEdge

states = CoverGroupDef("states", (
    CoverPointDef("state", {
        "busy": Bin.values(1),
        "complete": Bin.transition(0, 1, 2),
    }),
)).instantiate("core0.pipeline")

states.bind(
    trigger=RisingEdge(clock),
    fields={"state": dut.state},
    strategy="native",
)

async with Execution(backend, coverage=[states]):
    await run_case()
    checkpoint = states.report()

CoverageDatabase([states]).write_json("states.json")
```

`bind()` returns the same CoverGroup; it does not create another public component. Keys in
`fields` correspond to point sources and Iff field paths. Native sampling supports the full
unsigned width of XData, including values, ranges, masks, transitions, Iff, and same-sample
crosses above 64 bits. Counters remain checked 64-bit integers independently of signal width.
The execution strategy applies to the whole group:

- `native`: reject unsupported inputs at startup.
- `python`: process each sample in Python explicitly.
- `auto`: prefer native execution, otherwise fall back for the entire group. Reports expose the
  selected `sampling_backend` and the reason in `sampling_fallback`.

Collection does not advance the clock. Tests must still await triggers or perform transaction
operations. Each group has its own counts and history. A group cannot belong to two Executions
at once, or be sampled/merged manually while running. Close the backend only after its Execution
has exited. A group sampled by Execution also cannot receive manual samples afterward; use a
separate instance for transaction sampling so one result does not mix cycle and transaction units.

Wide values use the same definitions, for example the upper bits of a 128-bit payload:

```python
high = 1 << 100
payload_bins = CoverPointDef("payload", {
    "exact": Bin.values(high + 1),
    "range": Bin.range(high, high + 3),
    "mask": Bin.masked(high + 1, high + 3, width=128),
    "path": Bin.transition(high + 1, high + 2),
}, overlap="allow")
```

Matching and illegal-hit diagnostics preserve upper bits. X/Z in any bit increments `unknown`,
skips ordinary hits, and interrupts adjacent transitions, including mask don't-care bits.
Illegal values are captured at the hit; later signal changes or delayed `sync()` cannot alter
that evidence. Unsigned constants outside a signal's range are not truncated: unreachable
values/transitions do not hit, and ranges/masks retain integer semantics. Signed values and
arithmetic expressions over wide signals are outside this extension; native sampling still
requires direct XData bindings.

Run the complete example; both runs should produce identical bins and counts:

```bash
python3 examples/coverage/functional_coverage.py --wide
python3 examples/coverage/functional_coverage.py --wide --native --output wide.json
```

## Isolation and synchronization across Executions

Entering an Execution clears group counts and matching history by default. To retain completed
hits from earlier runs, bind with `accumulate=True`:

```python
states.bind(trigger=RisingEdge(clock), fields={"state": dut.state},
            strategy="native", accumulate=True)
for scenario in scenarios:
    async with Execution(backend, coverage=[states]):
        await scenario()
```

Matching history remains isolated even when counts accumulate. A trailing 0, 1 in one Execution
cannot combine with a leading 2 in another to hit `0→1→2`. When saving runs separately for later
merging, use default isolation; do not add cumulative snapshots as independent runs.

| Operation | Counts | Incomplete matches |
| --- | --- | --- |
| `report()` / `sync()` | Synchronize native counts without double counting | Retained |
| `clear_history()` | Retained | Cleared |
| `reset()` | Cleared; native statistics epoch updated | Cleared |
| Execution exit | Final synchronization; counts retained | Cleared; native registrations released |
| Next Execution entry | Cleared by default; retained with `accumulate=True` | Always restart |

During execution, call `sync()` before reading raw statistics such as `samples`. Reports,
coverage queries, and JSON export synchronize automatically. Synchronize on the Execution's
thread; after exit, the final report is safe to read. Normal exit, test exceptions, external
cancellation, and partial startup failure all release registrations. Snapshot failure does not
stop cleanup or discard the original test exception.

## Recognize complex patterns with existing triggers

```python
from toffee import Next, Sequence, Wait, xtrigger

@xtrigger(sample=RisingEdge("clock"))
def start_then_done(dut):
    return Sequence(Wait(dut.state == 1), Next(dut.state == 2))

states.bind(trigger=start_then_done(dut), fields={"state": dut.state},
            strategy="native")
```

The group samples once whenever the complete pattern finishes, using field values at completion.
The unit of `Bin.transition` becomes consecutive pattern-completion events. Within, Hold, and FSM
can also supply samples. Next requires success at the immediately following sample; otherwise it
waits for a new start. Sequence/FSM are non-overlapping, single-instance by default; coverage
bindings can explicitly enable bounded overlap. Ordinary `await trigger` semantics are unchanged.

Expression triggers honor their declared `enter`, `each_sample`, or `change` policy. A condition
that stays true samples once on entry with `enter`, or at every valid sample with `each_sample`.

A project may pass `abort=dut.abort_flag` explicitly and include it in the named fields mapping.
Abort takes precedence over same-sample completion, clearing history while retaining counts.
The framework does not infer signal protocol meanings. `@pytrigger` requires a contract such as
`contract="project-defined-name/v1"` to identify custom semantics and always samples in Python;
the framework does not manage custom closure state. Runs with different sampling rules cannot
be merged or accumulated together.

Run the existing examples:

```bash
python3 examples/coverage/functional_coverage.py --stateful
python3 examples/coverage/functional_coverage.py --stateful --native --output state.json
```

Native examples require xcomm Python bindings with `XTriggerEngine.CoverageVersion() == 5`.
Rebuild the bindings after changing the C++ backend. With an older version, `native` fails at
startup and `auto` falls back for the whole group.

## Optional concurrent matching

To observe new starts while an earlier pattern is still active, enable overlap on the binding:

```python
from toffee import Sequence, Wait, Within, xtrigger

@xtrigger(sample=RisingEdge("clock"))
def completion(dut):
    return Sequence(Wait(dut.start), Within(1, 3, dut.done))

group.bind(
    trigger=completion(dut), fields={"value": dut.value},
    overlap=True, max_active=8,
    diagnostics="summary",  # 独立开关；可以省略
)
```

- `overlap=False` is the default. Active matches reject new starts, and a completion sample is
  not reused as a new start.
- `overlap=True` requires an explicit positive `max_active` and supports only compiled
  Sequence/FSM patterns. An overlapping Sequence starts with Wait; every sample satisfying its
  start can create a match. This does not implicitly become edge detection.
- An FSM instance starts when it leaves the start state. A start-state self-loop creates none.
  A direct transition from start to a terminal counts as completion. Returning to start without
  reaching a terminal ends the attempt.
- Each instance advances at most one step per sample. Existing instances run before new starts,
  so same-sample completion can release capacity. An old instance's completion does not let a new
  instance advance a second step in that sample.
- A new start at full capacity raises an error and marks `collection_complete=False`; it is not
  silently dropped. Existing counts remain, but `assert_coverage()` rejects incomplete collection.
- Change configuration only outside Execution. Concurrency policy is part of the sampling
  contract; different policies cannot be accumulated or merged directly.

For starts in cycles 1 and 2 followed by done in cycle 3 within the configured window, two
patterns complete and the group samples twice. Both use the same completion condition; this
is not automatic request/response association. Dynamic keys and captured start fields are not
provided here. Use project Monitor/Scoreboard association for those cases. Fields come from the
completion phase, and temporal bins count completion events, possibly several in one cycle.

Run the full example:

```bash
python3 examples/coverage/functional_coverage.py --overlap
python3 examples/coverage/functional_coverage.py --overlap --native --output overlap.json
```

## Optional diagnostics and live inspection

`diagnostics="off"` retains only coverage counts and illegal-hit evidence.
`diagnostics="summary"` adds aggregates for complex sampling patterns and `Bin.transition`,
without storing a cycle-by-cycle trace:

| Field | Meaning |
| --- | --- |
| `started` / `completed` | Matches started / recognized as complete |
| `failed` | Next mismatch, or active FSM returning to start without completion |
| `expired` | Within exceeded its upper bound; success at the bound takes precedence |
| `aborted` | Active matches terminated by the explicit abort condition |
| `cleared` | Active history removed by clear_history, a bin gate, or unknown samples |
| `unfinished_at_close` | Matches still incomplete immediately before Execution exit |
| `peak_active` | Highest active-match count |

`completed` describes recognition by the matcher, not necessarily an ordinary bin hit: group
gates and ignore/illegal priority still apply. Diagnostics do not affect the coverage denominator;
incomplete or expired matches do not automatically fail a checker. HTML includes a collapsible
section only when summaries were collected. JSON stores them under `diagnostics.patterns`.

Inspect current state while running, without enabling summaries:

```python
async with Execution(backend, coverage=[group]):
    await ClockCycles(clock, 2)
    progress = group.inspect()
    # {"tick": ..., "phase": ..., "trigger": [...], "bins": {...}}
    # Sequence/transition: step（从 0 起）、age、held；FSM: state 名称。
```

Call `inspect()` on the Execution thread. It returns independent data without advancing time or
changing matching history. It is available only while Execution is active; afterward, use report
summaries. Closing records unfinished counts before freeing state. Normal close and external
cancellation do not count as protocol-level `aborted` events.

The next Execution resets statistics by default. `accumulate=True` retains counts and diagnostics,
never history. Merging sums event counts and takes the maximum peak. Enabling diagnostics does not
change sampling semantics. Mixed runs expose `collected_runs` / `uncollected_runs`; missing data
is not reported as zero. `clear_history()` retains statistics and records cleared matches;
`reset()` clears counts, diagnostics, and history.

## Save and merge

```python
from toffee import CoverageDatabase

database = CoverageDatabase([coverage])
database.write_json("functional-coverage.json")

merged = CoverageDatabase.read_json("run-a.json")
merged.merge(CoverageDatabase.read_json("run-b.json"))
merged.write_json("merged.json")
```

Only same-name instances with compatible schema digests and sampling contracts can merge safely.
Each report carries collection origins and a `snapshot_id`. Duplicate origins, duplicate run IDs
for an instance, and overlapping cumulative snapshots raise `CoverageMergeError` without changing
counts. Merge independent final snapshots, not intermediate and final snapshots from one run.
`accumulate=True` retains origins for overlap detection. Reset starts a new origin; when using
explicit run IDs, instantiate a new run with a new ID.

The database JSON format is version 2; schema versioning is independent. Cross counts use
unambiguous JSON-array encoding for IDs. `bins` stores ID, tuple, label, kind, and `at_least`;
labels are display-only. Readers accept the current format and do not convert older reports.

## Unified HTML reports

Convert Verilator coverage to LCOV first. Other simulators producing LCOV use the same entry point:

```bash
toffee-coverage-report \
  --functional functional-coverage.json \
  --line-coverage Verilator=line-coverage.info \
  --site coverage-report
```

The output contains functional group/point/cross pages, each bin's target/count/description,
per-file and per-line RTL coverage generated by genhtml, and shared navigation between functional
and line coverage. See the full
[functional_coverage.py example](https://github.com/XS-MLVP/toffee/blob/main/examples/coverage/functional_coverage.py).

## Sample transaction fields and apply gates

`source` accepts dotted paths through mappings, dataclass/object attributes, and BundleValue.
Separate design field names from coverage names for reusable definitions. Iff controls when to count:

```python
from toffee import Iff

point = CoverPointDef(
    "opcode", {"read": Bin.values(0), "write": Bin.values(1)},
    source="request.opcode", iff=Iff.equals("accepted", True),
    weight=2, goal=100,
)
```

Iff can gate a CoverGroupDef, CoverPointDef, or CrossDef. `Iff.not_equals()` excludes a condition;
`Iff("mode", (1, 2))` accepts several values. Reports expose gated sample counts in `gated`.
Missing fields are sampling errors, not silently skipped samples.

## Limit crosses and control overlap

By default, crosses form the Cartesian product of normal bin IDs. Use include to restrict a large space:

```python
cross = CrossDef(
    "operation_result", ("operation", "result"),
    include=(("read", "ok"), ("write", "ok")),
    ignore=(("read", "retry"),),
    illegal=(("write", "forbidden"),),
    at_least=2, max_auto_bins=64,
)
```

These strings are declared bin names, not raw input values. `max_auto_bins` limits automatic normal
cross expansion; excessive size raises `CoverageSchemaError` at definition time. A point's
`overlap="warn"` diagnoses overlaps by default, `"error"` rejects them, and `"allow"` permits them.
With overlap allowed, one sample can hit multiple bins and all corresponding cross combinations.

Priority is illegal, ignore, then normal; default bins record otherwise unmatched values.
Only normal bins enter the denominator. `IllegalHit` retains metadata, and `assert_coverage()`
still rejects illegal hits when `illegal_policy="record"` defers the exception.

A normal bin entirely excluded by ignore/illegal rules remains in the schema and diagnostic
counts but leaves the denominator, uncovered list, and automatic cross space. Reports explain
this in `excluded_bins`. A partly excluded bin remains: excluding 1 from `{0,1}` leaves 0 as the
required hit. Combined range/mask exclusions need not enumerate values; overly complex analysis
fails at definition time. Transition exclusion follows completion-time priority, including a
statically excluded final value or full coverage by an identical pattern. This does not imply
support for all SystemVerilog transition operators. A point/cross with no eligible normal bin
after exclusion is invalid.

## Check targets, find gaps, and reuse definitions

- `point_coverage(name)` / `cross_coverage(name)` return local percentages.
- `coverage` is the weighted normal-bin percentage across points/crosses. Weight zero still
  samples but contributes no weight.
- `goal_met` only compares the group's raw weighted percentage with its goal; each item also
  reports its own goal result.
- `covered` requires the group target, every positive-weight item's target, complete collection,
  and no illegal hits. This is coverage acceptance, not Scoreboard or whole-test success.
- `assert_coverage()` checks those same conditions. `minimum` overrides the group threshold;
  child items retain their goals. `per_item=False` checks only the group threshold while still
  rejecting incomplete collection and illegal hits. This differs from older default acceptance
  behavior; percentages are not normalized by goals.
- `uncovered()` lists bins below `at_least`. Half the required hits do not earn half a bin's credit.
- `sample()` returns CoverageSample by default, including point_bins, cross_bins, and illegal_hits.
  Use `details=False` when only accumulating counts on a hot path.
- `reset()` clears an instance's counts and illegal records but keeps its definition. Across runs,
  creating a new group usually makes artifacts easier to distinguish.

Same-name instance merges require matching definition digests and policies or raise
`CoverageMergeError`. Different instances keep separate results; `CoverageDatabase.type_reports()`
provides explicit type-level aggregation. JSON round trips retain definitions, counts, and
descriptions. Renaming is not a substitute for compatible coverage plans.

## Single-file reports and Python integration

To share one file:

```bash
toffee-coverage-report --functional functional-coverage.json --output coverage.html
```

`generate_unified_coverage_report(functional_paths=[...], output=...)` also generates self-contained
HTML. `generate_unified_coverage_site(..., output_dir=...)` generates a site. Only sites needing
LCOV source-line pages require genhtml; single-file reports and functional-only sites do not.
The output directory must be empty or carry an existing Toffee site marker. Other nonempty
directories are protected. LCOV source files must be readable where the report is generated;
genhtml controls displayed paths, without forced prefix stripping that changes source lookup.

Use `parse_lcov()`, `build_unified_coverage_model()`, and `render_unified_coverage_html()` to parse,
combine, and render existing data. Functional bins and simulator line coverage stay separate;
they are not averaged into one verification percentage. pytest retains native pass/fail results.
See [run artifacts](verification-flow.md) for a complete saving recipe and
[functional requirements across test cases](functional-points.md) for coverage planning.
