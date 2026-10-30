# Public API index

Common objects are exported from top-level `toffee`. Start with the
[beginner tutorial](../getting-started/index.md) if you are new to the framework.

| Need | Common entry points |
| --- | --- |
| Advance simulation and wait for conditions | Execution, ClockCycles, Value, AnyOf |
| Drive and sample structured data | Bundle, SignalDriver, Transfer |
| Track, compare, and finish transactions | XTransfer, Scoreboard; see [interface selection](../guides/scoreboard.md) |
| Functional coverage and reports | CoverGroupDef, CoverGroup, CoverageDatabase |

Backend extension protocols and low-level advancement types appear later. Ordinary tests do not
need to implement them.

## Execution and Backends

See [Execution reference](execution.md) for parameters, defaults, and resource contracts.

| API | Purpose |
| --- | --- |
| `Execution` | Async execution scope for one test |
| `Execution.external_task(coro, *, name=None)` | Create an out-of-domain native Task without implicit Reactor binding; caller owns cleanup |
| `Execution.paused()` | Freeze simulation time while host asyncio keeps running |
| `SimulationNotSettledError` | Ready work exceeded max_settle_rounds; the clock does not cross the barrier |
| `XCommClockBackend` | XClock/C++ trigger engine adapter |
| `MemoryBackend` | Pure Python Backend for semantic tests |

## Triggers and events

| API | Purpose |
| --- | --- |
| `RisingEdge`, `FallingEdge`, `DriveStable` | Stable-phase triggers |
| `ClockCycles` | Count rising-edge cycles |
| `Value`, `ValueChange` | Value conditions and changes at a selected phase |
| `SimTimeout`, `WallTimeout` | Return a TIMEOUT event on expiry; caller decides whether to raise |
| `AnyOf`, `AllOf` | Compose waits |
| `AsyncioEventTrigger`, `QueueTrigger`, `TaskComplete` | Adapt asyncio events into waits |
| `XEvent`, `EdgeEvent`, `ConditionEvent`, `FsmEvent` | Await results |
| `XPhase`, `XEventKind`, `LogicValue` | Phases, event kinds, and four-state values |

## Compilation and subscriptions

| API | Purpose |
| --- | --- |
| `@xtrigger` | Conditions/Sequence/FSM compiled into native IR |
| `@pytrigger` | Explicit Python predicate fallback |
| `Next(expr)` | Strictly adjacent Sequence sample; mismatch restarts at the beginning |
| `Bin.transition(*values, overlap=True)` | Adjacent-sample transition bin reusing Sequence matching semantics |
| `CoverGroup.bind(trigger=..., fields=..., strategy="auto", accumulate=False, overlap=False, max_active=None, diagnostics="off")` | Configure passive collection, optional bounded concurrent patterns and diagnostics, managed by Execution |
| `CoverGroup.sync()` / `clear_history()` | Synchronize native statistics / clear incomplete matches only |
| `CoverGroup.inspect()` | Inspect current steps, states, and progress inside Execution without traces or enabled diagnostics |
| `Sequence`, `Wait`, `Within`, `Hold` | Non-overlapping patterns by default; coverage may enable concurrency; missing a match does not automatically fail a test |
| `FSM`, `State` | Explicit branching state machines |
| `@on` | Declare a persistent subscription |
| `Execution.subscribe` | Start a bound subscription |

## Data and protocols

| API | Purpose |
| --- | --- |
| `as_xdata` | Obtain/validate native XData |
| `Bundle`, `BundleValue` | Structured live-signal view and immutable snapshot |
| `PackedArray`, `split_packed` | Split a packed bus into equal-width elements |
| `PackedLayout`, `PackedView` | Nested, customized packed layouts |
| `ReadyValid`, `Decoupled`, `Role` | Optional ready/valid channel views |
| `Driver`, `SignalDriver`, `Monitor` | Component interfaces and Bundle-independent signal ownership base |
| `Agent` | Interface instance combining Driver/Monitors; connect(ref), send/submit/recv, drain/finish; managed by Execution |
| `ReadyValidMonitor` | Passively observe ready/valid handshakes |
| `SamplingMonitor` | Generic explicit-trigger/synchronous-capture template retaining the triggering event |
| `MonitorClosedError`, `MonitorOverflowError` | Built-in Monitor termination and lossless-capacity errors |
| `SyncDriver`, `SyncSingleCycleDriver`, `SyncMultiCycleDriver` | Await send for acceptance; generic, single-cycle, and fixed-stage templates |
| `AsyncDriver`, `AsyncSingleCycleDriver`, `AsyncMultiCycleDriver` | Background submission base and timing templates; send/submit submit immediately when called |
| `DriveStage` | Static stage description: signals, idle values, encoding, and occupied cycles |
| `AsyncDriver.send(transaction) -> XTransfer[RequestT, XEvent]` | Complete automatically at acceptance; await returns acceptance event without a response collector |
| `AsyncDriver.submit(request) -> XTransfer[RequestT, ResponseT]` | Return a response handle completed by response association |
| `Transfer` | Monitor-delivered transaction and acceptance event |

## Functional coverage

| API | Purpose |
| --- | --- |
| `Bin`, `BinSpec`, `BinKind` | Bin definitions |
| `CoverPointDef`, `CrossDef`, `CoverGroupDef` | Serializable coverage schemas |
| `CoverGroup` | Runtime instance and counters |
| `CoverageDatabase` | Save, load, and merge multiple instances |
| `Iff` | Group/point/cross gates |
| `IllegalPolicy`, `OverlapPolicy` | Illegal-hit and overlap policies |
| `generate_unified_coverage_site` | Multi-page functional + LCOV report |
| `generate_unified_coverage_report` | Single-file unified report |

Import these from `toffee`. For class-based models, import the following from `toffee.declarative`:

| API | Purpose |
| --- | --- |
| `@coverpoint`, `CoverPoint`, `Bin` | Reusable classes declaring binning rules |
| `@covergroup`, `CoverGroup`, `Cross` | Sample types, points, and crosses |
| `Fields`, `wire` | Select field paths and bind existing signals |
| `TemporalCoverPoint`, `SignalCoverGroup` | Count events spanning cycles directly |

See [class declarations](../guides/coverage-classes.md) and [temporal coverage](../guides/coverage-patterns.md).
The declarative CoverGroup is distinct from the top-level class. Its `runtime` integrates with
existing execution and reporting.

## Transactions and Scoreboard

| API | Purpose |
| --- | --- |
| `XTransfer`, `TransferState`, `MISSING` | Awaitable request handle from submission through acceptance to completion/failure; cancelling a waiter does not cancel the transaction |
| `Scoreboard` | Direct checking or asynchronous association with bound Driver/Monitor |
| `CheckContext`, `ScoreboardStatus`, `Difference` | Comparison context, diagnostics, and field differences |
| `ScoreboardMismatch` | Expected/actual mismatch; an AssertionError subclass |
| `ScoreboardAssociationError` | Association conflicts, unmatched output, or observation-capacity overflow |
| `ScoreboardIncompleteError` | Transactions remain incomplete at close |
| `ScoreboardTimeoutError` | Cycle-budget failure with operation, budget_cycles, elapsed_cycles, tick, context, and other structured attributes |
| `DriverIncompleteError` | Accepted requests remain incomplete when Driver closes |
| `MissingExpectedError` | Async transaction lacks explicit expected value or expected source |
| `structural_differences` | Recursive differences across dataclasses, mappings, sequences, and BundleValue |

See [Scoreboard and pipelined transactions](../guides/scoreboard.md) for complete examples.

## Backend and component extension

| API | Purpose |
| --- | --- |
| `SimulationBackend` | Backend protocol |
| `BackendCapabilities` | Supported phases/capabilities |
| `RunLimit`, `RunResult`, `StopReason` | Batched Backend advancement interface |
| `AcceptedDriver`, `ObservedMonitor` | Structural component Protocols used by async Scoreboard |

## Coverage and run reports

See [Runner reference](runner.md) for test CLI, artifacts, and plugin interfaces.

| API | Purpose |
| --- | --- |
| `CoverageSample`, `IllegalHit`, `IllegalBinError` | Sample hit details, illegal records, and failure |
| `CoverageSchemaError`, `CoverageMergeError` | Invalid definitions or incompatible merges |
| `CoverGroupDef.instantiate(..., run_id=None, run_metadata=None, contract=None)` | Run identity, context, and manual transaction sampling contract |
| `CoverGroup.goal_met`, `CoverGroup.covered` | Group percentage goal / acceptance including child goals, illegal hits, and completeness |
| `CoverGroup.assert_coverage(minimum=None, per_item=True)` | Check positive-weight child goals by default; optionally check only group threshold |
| `CoverGroup.from_report(report)`, `CoverageDatabase.read_json(path)` | Read current-format reports/databases |
| `parse_lcov` | Parse simulator line coverage and merge records for each file |
| `build_unified_coverage_model`, `render_unified_coverage_html` | Compose data explicitly and render self-contained HTML |

See [coverage](../guides/coverage.md) for sampling, crosses, gates, saving, and merging.

## Declaration types and low-level handles

These types support component development and diagnostics. Ordinary tests should start with the
feature entry points above.

| API | Purpose |
| --- | --- |
| `Field` | Source/width declarations for Bundle.bind/view_as; see [data](../guides/data.md) |
| `drive_ready_valid` | Optional handshake helper for project Drivers; see [protocol views](../guides/protocols.md) |
| `XTrigger`, `PhaseTrigger`, `ConditionMode` | Trigger contract, sample phase, and match mode |
| `PythonPredicateTrigger`, `CompiledTrigger` | Explicit Python predicate and IR condition instances |
| `PhaseEvent` | Events for phases such as DriveStable |
| `XExpr`, `SequenceSpec`, `FsmSpec` | Compiled expressions and timing specifications; prefer decorators/constructors for declarations |
| `XSubscriptionSpec`, `BoundSubscriptionSpec` | @on declarations and bound results; see [subscriptions](../guides/subscriptions.md) |
| `Subscription`, `SubscriptionOverflowError` | Running subscription handle and lossless queue overflow |
| `Reactor`, `Registration` | Component-level registration, dispatch, and cancellation managed by Execution |
| `BackendHandle`, `BackendHit` | Backend arm/disarm identity and hit records |
| `ScoreboardMode`, `ScoreboardError` | Direct/async checking modes and DUT checking exception base |

The authoritative export list is `__all__` in `src/toffee/__init__.py`. See the [feature map](feature-map.md)
for examples/regressions and [semantics and limitations](semantics-and-limitations.md) for boundaries.
