# Migrate to Toffee 2

Toffee 2 migrates the XReactor 1.1.0 implementation, providing driving, monitoring, reference models,
checking, and coverage. **Toffee Legacy's implementation and history remain on
[`master`](https://github.com/XS-MLVP/toffee/tree/master); the new implementation is on `main`.**
The old distribution is `pytoffee`; the new one is `uc-toffee`. Both import as `toffee`, but their
APIs and execution semantics are incompatible. Use separate virtual environments. Existing
verification environments need updates to execution scopes, signal binding, model connections,
and sampling semantics.

The migration baselines are [XReactor 1.1.0 `3017293`](https://github.com/XS-MLVP/xreactor/tree/3017293a7597c39adbae883e15d63d727bcbf6ae)
and [Legacy Toffee `f57dd6f`](https://github.com/XS-MLVP/toffee/tree/f57dd6ffc295b9b8bad3e3b3ce040b8a849f5c91).
Legacy actually released in the `0.x` series, not 1.x. Toffee 2 starts at `2.0.0` under `uc-toffee`;
install with `python3 -m pip install "uc-toffee[runner]"`.

## Choose a path for existing projects

Projects staying on Legacy should retain dependency locks. Git installations should select
master or a validated old commit explicitly instead of following the changed default branch.
Old 0.x tags still refer to Legacy.

To migrate:

1. Create a separate Python 3.11+ environment and install `uc-toffee[runner]`.
2. Migrate test entry points, Bundle, Driver, Monitor, and model wiring using the tables below.
3. Review cycle boundaries, completion conditions, and cleanup against the sampling/waiting sections.
4. Run project regressions and validate results before updating dependencies.

Use separate environments for comparison because both generations provide `import toffee`.

## Move from XReactor 1.1

Replace Python imports `xreactor` with `toffee`; commands `xreactor` and `xreactor-coverage-report`
become `toffee` and `toffee-coverage-report`. The distribution is `uc-toffee`. The low-level public
class XReactor becomes Reactor. Rename the `pytest_xreactor_coverage` hook,
`xreactor_register_dut` fixture, and `xreactor_functional_coverage` marker to use the toffee prefix.
The bundled runner continues to use pytest, pytest-asyncio, and pytest-xdist; it needs no toffee-test.

Coverage JSON's `xreactor-*` format identifiers and existing report-directory markers remain
compatible, so existing reports are readable. This naming migration does not change the native
dependency pin, ABI, or coverage protocol.

## Main differences

| Area | Toffee Legacy | Toffee 2 |
| --- | --- | --- |
| Advancement | Python clock task calls dut.Step(1), scans host-loop tasks, and waits for runnable coroutines | Execution observes this simulation's immediate callbacks; native Backend batches advancement until events/barriers |
| Conditions | Value/Condition wake and evaluate in Python each cycle | Compilable conditions, Sequence, and FSM evaluate in C++; Python predicates explicitly use @pytrigger |
| Sampling | Mainly follows DUT, Bundle, or pin cycle events | Explicit rising, falling, or drive-stable phase via sample/default_sample |
| Wait results | Coroutine triggers generally indicate completion only | Reusable specifications with independent registrations per await, returning XEvent tick/phase data |
| Signals | Bundle subclasses declare Signal/Signals and bind by prefix, regex, or mapping | Bundle combines live signals, explicit fields, trees, and packed views; sample returns immutable snapshots |
| Agents | Multiple decorated driver/monitor methods inside an Agent | One optional Driver plus named Monitors, with submission and response checking |
| Models | Hook/Port wiring through Env.attach path matching | Plain Python model connected with agent.connect(model); automatic response chain calls accept after actual acceptance |
| Checking | Compare driver returns with Hook results, or monitor/model ports | Independent Scoreboard: direct, FIFO, key, or fixed-latency checking, cycle budgets, and end checks |
| Concurrency | Executor groups, priorities, and model-first ordering | Driver manages inputs/resources; ordinary async scenarios and asyncio for independent concurrency |
| Lifecycle | Monitor tasks created during construction; standard shutdown cancels other loop tasks | Execution manages registered Agents/framework resources; creators own independent tasks |
| Functional coverage | CovGroup binds objects and Python predicates, with function labels | Separate definitions/instances, bins, crosses, transitions, native collection, provenance, and merge |
| Test entry | toffee.run and separate toffee-test plugin | Standard pytest/pytest-asyncio; fixtures or project recipes retain artifacts |

See Legacy [asynchronous.py](https://github.com/XS-MLVP/toffee/blob/f57dd6ffc295b9b8bad3e3b3ce040b8a849f5c91/toffee/asynchronous.py)
and [triggers.py](https://github.com/XS-MLVP/toffee/blob/f57dd6ffc295b9b8bad3e3b3ce040b8a849f5c91/toffee/triggers.py).
For current responsibilities, see [Execution](execution-and-backend.md) and [Agents/models](agents-and-reference-models.md).

Native batching reduces Python scheduling and cross-language calls on unmatched cycles. Per-cycle
Python Drivers, Monitors, and predicates limit that benefit. Compare actual projects with identical
inputs, sampling scope, and simulator options. Empty-clock measurements in the
[benchmark guide](https://github.com/XS-MLVP/toffee/blob/main/benchmarks/README.md) compare framework
overhead and cannot directly predict real DUT speedup.

## Installation and execution scope

Legacy requires Python 3.8+; Toffee 2 requires 3.11+. From the Toffee repository root:

```bash
python3 -m pip install -e '.[test]'
```

Real DUTs need xcomm/xspcomm bindings with XTriggerEngine, half-step, and stable-sampling support.
Whether an existing Picker DUT can be reused depends on its exposed XClock/XData and native
binding capabilities. Installing Python code cannot add capabilities to old native bindings.
See [native clocks and project DUTs](../getting-started/native-and-next.md).

Replace start_clock(dut) plus toffee.run with Execution inside an existing asyncio loop. Here the
project creates dut, and dut.clock is its registered clock pin:

```python
from toffee import ClockCycles, Execution, RisingEdge, XCommClockBackend

backend = XCommClockBackend(dut.GetXClock())
try:
    async with Execution(backend, default_sample=RisingEdge(dut.clock)):
        await ClockCycles(dut.clock, 1)
finally:
    backend.close()
```

Execution exit clears current registrations/components while retaining Backend and DUT state.
The Backend fixture closes it; the DUT fixture releases the DUT through its generated interface.
Define reset explicitly when reusing a Backend. Recreate closed Agents and built-in Monitors.
Never advance one DUT concurrently with dut.Step, a Legacy clock task, and Execution.

## Common API mappings

These mappings indicate how to rewrite code; identical names still require the semantic review below.

| Legacy usage | Current usage and caveats |
| --- | --- |
| toffee.run(test), start_clock(dut) | Ordinary asyncio entry or pytest-asyncio, with async Execution scope |
| await bundle.step(n), await ClockCycles(dut, n) | await ClockCycles(clock, n), passing XClock or its registered clock pin; n must be positive |
| await Value(pin, value, delay=...) | await Value(signal, value, sample=...); handle Legacy delay separately |
| await Condition(item, predicate) | @xtrigger for compiled expressions; @pytrigger or PythonPredicateTrigger for arbitrary synchronous Python |
| await AllValid(*pins) | Explicit @xtrigger condition requiring all signals valid |
| await Change(pin) | await ValueChange(signal, sample=...) |
| Delayer(signal, delay) | Capture history at an explicit phase; use Scoreboard latency association for fixed-latency responses |
| RisingEdge(pin), FallingEdge(pin) | Clock stable phases; express sampled edges of arbitrary data signals separately |
| Event, Queue, sleep, create_task, gather | Native asyncio objects; adapters when composing with DUT triggers |
| Signal/Signals/Bundle subclass declarations | Bundle(...), Bundle.bind(..., Field(...)), or Bundle.bind_tree(...) |
| bundle.assign(...) | bundle.drive(...) for complete structures; explicit leaf writes for partial updates |
| bundle.as_dict() for current values | bundle.sample() snapshot, then snapshot.as_dict() if needed |
| @driver_method | Project Driver plus Agent send/submit; project helpers may wrap protocol operations |
| @monitor_method | Explicit sampling trigger and synchronous capture, usually SamplingMonitor |
| Env.attach(Model()), @driver_hook | Environment explicitly constructs Agent/plain model and calls agent.connect(model) |
| @monitor_hook, model Port | Explicit observation consumption/model/checker wiring; no same-name autowiring |
| CovGroup, add_watch_point | CoverGroupDef, CoverPointDef, Bin, then instantiate and sample |
| @toffee_test.testcase, @toffee_test.fixture | @pytest.mark.asyncio, @pytest.fixture, or @pytest_asyncio.fixture |

## Changed sampling and waiting semantics

### Select clock and phase explicitly

Legacy RisingEdge/FallingEdge compared pin values read each cycle. Current triggers represent
stable half-cycle barriers of XClock; the native Backend requires its own XClock or a registered
clock pin. Legacy RisingEdge(dut.irq) cannot simply be retained. To detect a clock-sampled 0→1 irq,
use a stateful Python predicate or timing pattern. These observe changes between selected samples,
not glitches occurring between them.

Use ClockCycles for ordinary cycle waits. A rising-edge acceptance protocol can encapsulate
falling-edge writes and next-rising acceptance in its Driver. DriveStable samples combinational
logic after falling-edge writes; see [triggers](triggers.md).

### Review Legacy delay boundaries

Legacy Value(..., delay=1) waited at least one cycle event before checking; delay=0 could check
immediately. Current Value first evaluates at the next explicit sample after registration, even
if its condition is already true.

If delay=N meant checking from the Nth rising edge, wait N−1 edges before registering Value for
N>1. Waiting N edges first delays earliest checking until edge N+1. This mapping assumes the
Legacy cycle event and chosen rising sample mean the same thing.

```python
# 在已有 Execution 内，从第三个后续上升沿开始判断。
await ClockCycles(clock, 2)
event = await Value(dut.ready, 1, sample=RisingEdge(clock))
```

Implement delay=0 by explicitly reading the current stable value and deciding whether to wait.
A one-shot Value defaults to enter mode with previous state false at registration. Persistent
observation on every valid cycle requires each_sample; otherwise consecutive valid=1 cycles
produce only one entry event.

### Separate compiled and Python conditions

Legacy Condition evaluates Python every cycle. Signal conditions can become:

```python
from toffee import RisingEdge, xtrigger

@xtrigger(sample=RisingEdge("clock"))
def accepted(dut):
    return (dut.valid != 0) & (dut.ready != 0)

# 在 Execution 内调用：event = await accepted(dut)
```

Use `&`, `|`, and `~` for Boolean composition. Unsupported Python operations raise rather than
silently falling back. Explicitly choose @pytrigger for lookups or complex object computation,
retaining its per-sample Python cost. Sequence/FSM recognize patterns; window mismatch does not
automatically fail a test. Configure a failure deadline separately.

### Handle results and timeouts

Awaiting a trigger returns XEvent describing hit tick, phase, and value. Live signals may change
before later async processing or composed waits complete; retain relevant values with synchronous
Monitor capture.

SimTimeout/WallTimeout return TIMEOUT events. Callers must raise explicitly for test failure.
AnyOf is a general race; Scoreboard response budgets enforce strict transaction cycle deadlines.
See [timeout semantics](triggers.md) and [Scoreboard](scoreboard.md).

## Migrate Bundle and data

Legacy subclasses Bundle and binds using prefixes, regexes, or mappings. Current Bundle directly
organizes actual signals, or validates names and widths through explicit field mappings:

```python
from toffee import Bundle, Field

request = Bundle.bind(dut, {
    "address": Field("io_req_addr", width=32),
    "data": Field("io_req_data", width=64),
})
request.drive({"address": 0x8000, "data": 3})
snapshot = request.sample()
assert snapshot.data.as_int() == 3
```

Bundle.drive requires a complete structure without extra fields. Rewrite Legacy partial assignment,
wildcards, and custom __bundle_assign__ behavior as project encoding or explicit leaf writes.
Bundle.bind_tree requires a project signal tree; bind explicitly if metadata is absent. Do not
apply Legacy prefix/regex binding code unchanged to the current class.

Snapshot leaves are LogicValue objects preserving width and X/Z; as_int raises on unknown bits.
Keep integer model calculations, but convert explicitly at boundaries and define unknown-input
handling. Native expressions require explicit width and signedness where relevant; review negative
constants, truncation, and casts using [expressions and resources](expressions-and-resources.md).
See [data](data.md) for packed arrays and structures.

## Migrate Drivers, Monitors, and models

Legacy Agents contain driving/monitoring methods with Env-injected Hooks/Ports. Current components
separate driving, sampling, prediction, and comparison; Agent connects one interface's components.
Encode a Legacy Agent's operations as different request types for one Driver, or use separate
Agents for independent input resources.

Legacy driver Hooks offered model_first/dut_first and priorities. The current automatic response
chain invokes synchronous model.accept **after actual input acceptance**, stores expected data,
and associates the response. Model state follows acceptance order; queued requests must not
advance it prematurely. Preserve functional calculations but rewrite decorators, path matching,
and scheduling configuration.

accept returns this request's independent expected response synchronously. Explicit expected
overrides comparison data while still updating the connected model. Legacy None skipped comparison;
current None is a valid expected value and cannot mean “no response.”

### A runnable migrated environment

The following file uses the teaching DUT: operand accepts at a rising edge with enable=1, and an
8-bit accumulated result responds two cycles later. With no ready backpressure, a single-cycle
Driver suffices. Real protocols must define acceptance and idle behavior themselves. Save the
example under the filename used by the command below in the repository root:

```python
import pytest
import pytest_asyncio

from examples.getting_started.toy_dut import TutorialDut
from toffee import (
    Agent, Bundle, Execution, PythonPredicateTrigger, RisingEdge,
    SamplingMonitor, SyncSingleCycleDriver,
)


class AccumulatorModel:
    def __init__(self):
        self.total = 0

    def accept(self, operand):
        self.total = (self.total + operand) % 256
        return self.total


@pytest_asyncio.fixture
async def accumulator():
    dut = TutorialDut(latency=2)
    driver = SyncSingleCycleDriver(
        dut.clock,
        Bundle(enable=dut.enable, operand=dut.operand),
        idle={"enable": 0, "operand": 0},
        encoder=lambda operand: {"enable": 1, "operand": operand},
    )
    monitor = SamplingMonitor(
        PythonPredicateTrigger(
            "response-present", lambda: dut.response is not None,
            sample=RisingEdge(dut.clock), mode="each_sample",
        ),
        capture=lambda event: dut.response,
    )
    agent = Agent(
        "accumulator", driver=driver, monitors={"response": monitor},
        response_monitor="response", clock=dut.clock,
        response_timeout_cycles=3,
    )
    agent.connect(AccumulatorModel())
    try:
        async with Execution(dut.backend, agents=[agent]):
            yield agent
    finally:
        dut.close()


@pytest.mark.asyncio
async def test_accumulator(accumulator):
    for operand in [250, 10]:
        await accumulator.send(operand)
    await accumulator.finish(timeout_cycles=10, observe_cycles=1)
```

```bash
python3 -m pytest -q test_toffee_新版_example.py
```

This uses a Python predicate for the teaching DUT's Python outputs. Real XData conditions can
compile. No Model base class, Hook decorators, or Env.attach are needed.

Legacy Monitor queues logged errors and dropped new records when full. Current Monitors and
subscriptions default to bounded lossless delivery, propagating overflow as verification failure.
Use delivery="latest" only for telemetry explicitly permitting older data to be discarded.
Response checking needs every record.

### send and submit wait for different endpoints

| Call/configuration | Caller waits for |
| --- | --- |
| await agent.send(request) | Input acceptance; configured response checking continues in the background |
| transfer = agent.submit(request), with AsyncDriver and response checking | Immediate submission; await transfer returns the associated, checked response |
| await agent.drain(timeout_cycles=...) | Transactions submitted before the call; later submission remains allowed |
| await agent.finish(timeout_cycles=..., observe_cycles=...) | Seal, drain, trailing observation, and residual checks; Execution still closes Monitors |

Code that previously used a driver method's DUT return value should use a response-checking
AsyncDriver Agent's submit or an explicit project response path. SyncDriver Agents have no submit.
AsyncDriver.send returns an awaitable handle, not a coroutine for asyncio.create_task; save and
await the handle directly.

Returning from a test body does not mean responses have been checked. On success, call budgeted
finish after submission ends and choose an observation window for allowed latency and extra
responses. Unawaited background errors still propagate at finish or Execution exit.

Cancelling a coroutine waiting on a handle does not cancel the transaction. Explicit cancellation
only applies before acceptance. The environment defines reset/flush and stale-response handling
after inputs have been accepted.

### Wire passive models and complex responses explicitly

Automatic connect currently pairs one actively accepted request with one response. Legacy
AgentPort, DriverPort, MonitorPort, multi-model Hooks, and multi-interface Hooks have no one-for-one
compatibility layer. For passive inputs, multiple responses, or DUT-initiated transactions, use
input/output Monitor records and project-defined model update/association rules.

Agents can share a model, but the framework does not infer same-tick interface arbitration or
update order. Express hardware ordering formerly encoded in priority/model-first settings in the
environment. A checker-owned response Monitor cannot also be consumed by a test's recv; Queue
consumers split records. Broadcast immutable observations explicitly when needed.

## Migrate Executor and external async tasks

There is no direct replacement for Legacy Executor groups, priority, sche_order, or exit policies.
AsyncDriver queues one input interface. Ordinary async functions express sequential scenarios;
asyncio.TaskGroup can run independent interfaces concurrently. The project defines group ordering.

Use native asyncio Task, Lock, Queue, and Event. XEvent is an already-observed simulation result;
replace Legacy toffee.Event() with asyncio.Event(). Use AsyncioEventTrigger, QueueTrigger, or
TaskComplete to compose external waits with DUT events.

Long-lived services can run outside the simulation domain through execution.external_task(coro);
creators still own shutdown. Infinite in-domain sleep(0) loops prevent settling and may raise
SimulationNotSettledError. External tasks cannot directly await DUT triggers; deliver their results
to simulation tasks instead.

HTTP or other external I/O does not automatically pause the clock while simulation watchers remain.
Use execution.paused() to freeze time during configuration, and leave the pause before waiting on
clock-dependent triggers. See [asyncio and pytest](asyncio-and-pytest.md).

## Migrate coverage and reports

Legacy CovGroup combines live objects and Python conditions. Current coverage separates static
definitions, runtime instances, and sampling.

| Legacy | Current |
| --- | --- |
| Eq(value), In(values) | Bin.values(...) |
| IsInRange(low, high) | Bin.range(low, high) |
| add_watch_point(target, bins, name) | CoverPointDef(name, bins), with sampled fields or XData binding |
| Callable/dynamic bins | Compute transaction fields and define stable bins; no arbitrary callable-bin equivalent |
| is_all_covered() | covered or assert_coverage(...), after reviewing denominators and thresholds |
| mark_function(...) | Functional descriptions and actual hit provenance; old function labels do not become hits |
| as_dict() and Legacy reports | Current report()/CoverageDatabase schema; old JSON cannot be directly merged as current databases |

For example, convert operand boundary coverage to transaction sampling:

```python
from toffee import Bin, CoverGroupDef, CoverPointDef

coverage = CoverGroupDef("input", (
    CoverPointDef("operand", {
        "zero": Bin.values(0),
        "maximum": Bin.values(255),
        "middle": Bin.range(1, 254),
    }),
)).instantiate("accumulator.input")
coverage.sample({"operand": 0})
coverage.sample({"operand": 255})
coverage.sample({"operand": 3})
coverage.assert_coverage(100.0)
```

This manually samples supplied values. Real transaction coverage must consistently sample at its
defined submission, acceptance, or observation point. To record actual acceptance, attach sampling
after the acceptance event. Bind cycle-level signal coverage to a clock and register it through
Execution(coverage=[group]).

Legacy CovGroup stopped sampling after full coverage by default. Current coverage keeps accumulating
and uses at_least for hit targets, so counts need not match. Cross-run merges require compatible
schemas and sampling rules; run IDs and metadata preserve origins. See [coverage](coverage.md)
for crosses, illegal/ignore bins, transitions, and native strategies.

DUT creation, cycle sampling, and --toffee-report in [toffee-test](https://github.com/XS-MLVP/toffee-test)
belong to that independent plugin. Toffee 2 does not provide the same entry points. Use standard
pytest, with project fixtures owning DUTs, waveforms, RTL coverage, seeds, and cleanup. Current
unified reports combine functional coverage and LCOV; they are not the old plugin's complete pytest
execution report. pytest's pass/fail remains the verdict. See [run artifacts](verification-flow.md).

## Current migration boundaries

- A native Backend represents one XClock scheduling domain. Independent Executions can share a
  host loop but have no common multi-clock timeline; review old multi-clock tests separately.
- Native expressions support arbitrary-width bit vectors with explicit signed/unsigned types
  and supported arithmetic. Review width, overflow, and unknown-value rules; this is not complete
  SystemVerilog expression compatibility.
- Ordinary Sequence/FSM waits are non-overlapping. Coverage's bounded overlap does not imply all
  triggers overlap or provide a full temporal assertion system.
- Backend, XClock, and DUT signals belong to the Execution thread. One Backend cannot serve
  simultaneous active Executions.
- Model Ports, Executor priorities, Bundle randomization, and bulk assignment lack compatibility
  entry points. Random stimulus can remain request generators using local random.Random(seed).
- The scheduling observer targets asyncio BaseEventLoop; other loops require adaptation/validation.
- SystemRDL register support is not implemented and is unavailable for migration.

See [semantics and limitations](../reference/semantics-and-limitations.md).

## Migrate one environment at a time

1. Fix input sequences, seeds, DUT configuration, and expected results; record key acceptance/response cycles.
2. Update Python/native bindings and verify reads, writes, and waits in one Execution.
3. Migrate Bundle bindings and check fields, widths, shape, partial updates, and unknown values.
4. Separate Driver and Monitor behavior; define acceptance and immutable response capture.
5. Retain model calculations, rewrite wiring/update timing, and select FIFO, key, or fixed latency.
6. Replace entries/fixtures, add budgeted finish, and assign component/DUT/external-task cleanup.
7. Rebuild coverage definitions/sampling rules, then review reporting and cross-run merging.

Compare acceptance counts, response counts, keys, data, and protocol-permitted latency for identical
inputs, not only final values or passing-test counts. Existing backpressure, continuous valid,
reordering, missing/extra responses, cancellation, and reset scenarios should retain their expected behavior.

Run tutorials and the reordered-model example to confirm API usage:

```bash
python3 -m pytest -q examples/getting_started/test_accumulator.py
python3 -m examples.transactions.reference_model
```

With native bindings, run native regressions as well:

```bash
python3 -m pytest -q --require-xspcomm
python3 -m examples.transactions.reference_model --backend native
```

Framework and teaching-example checks do not replace project DUT comparisons between Legacy and Toffee 2.
