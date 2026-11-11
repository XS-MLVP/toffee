# Use Triggers

A Trigger describes a wait condition. `await trigger` returns an `XEvent`.
These snippets run inside an active Execution with project-provided clocks and signals.
For a complete test, see the [clock tutorial](../getting-started/clock-and-input.md).

## Combinational logic without clock advancement

Use `Execution` for combinational tests too, rather than manually calling `RefreshComb()` inside it:

```python
from toffee import CombStable, Execution, XCommClockBackend

backend = XCommClockBackend(dut)
try:
    async with Execution(backend):
        dut.a.AsImmWrite()
        dut.b.AsImmWrite()
        dut.a.Set(1)
        dut.b.Set(2)
        event = await CombStable()
        assert dut.sum.U() == 3
finally:
    backend.close()
```

This assumes a project-provided adder DUT. No `InitClock` or artificial clock signal is needed.
For a runnable example without RTL, see [combinational.py](https://github.com/XS-MLVP/toffee/blob/main/examples/triggers/combinational.py).

`CombStable(source=None)` requests evaluation and sampling after the current domain's ready work
settles. It generates no edges and does not change the half-tick. It returns a `PhaseEvent` with
kind `COMB_STABLE`. Requests in the same batch share an event; successive samples at the same tick
have increasing `event.delta`, reset when the clock advances. An optional source must belong to
the current Backend; normally use `CombStable()`. Evaluation preserves signal write modes:
use `AsImmWrite()` for combinational inputs; edge-deferred writes are not committed early.

Conditions accept `sample=CombStable()` or `Execution(default_sample=CombStable())`.
Registering a condition requests one initial sample; an unsatisfied condition subsequently observes
new explicit requests. `SamplingMonitor(CombStable(), ...)`, subscriptions, and bound coverage are
passive: they request no evaluation and never advance time themselves. Drivers should
`await CombStable()` after each batch of input writes. Use wall-time budgets for clockless waits,
not a `SimTimeout` that cannot expire without clock advancement.

Native support requires xcomm `CombinationalVersion() == 1`. Conditions, Sequence/FSM, and supported
coverage models remain in the native engine without an implicit Python fallback. `@pytrigger`
and explicitly selected Python coverage retain their Python semantics. `DriveStable(clock)` still
means the post-falling-edge drive window; it is not an alias for `CombStable()`.

## Clocks

Express ordinary test waits in cycles; let Drivers and Monitors encapsulate precise drive and sample timing:

```python
await ClockCycles(dut.clock, 1)
await ClockCycles(dut.clock, 10)
```

`ClockCycles(clock, n)` waits for n rising edges after registration and returns the last stable event.
The first wait may begin partway through a cycle. Backpressured protocols must wait for actual acceptance;
elapsed time alone does not prove a handshake.
For phase-sensitive components or fault injection:

```python
await FallingEdge(dut.clock)
await RisingEdge(dut.clock)
await DriveStable(dut.clock)
```

FallingEdge and RisingEdge return at stable half-steps. For an interface that samples inputs on rising edges:

```python
await FallingEdge(dut.clock)
dut.valid.Set(1)
dut.data.Set(payload)
await RisingEdge(dut.clock)
```

## Signal conditions

```python
await Value(dut.ready, 1, sample=RisingEdge(dut.clock))
await ValueChange(dut.response, sample=RisingEdge(dut.clock))
```

An omitted `sample=` uses `Execution(default_sample=...)`. Evaluation starts at the next sample after registration.

| Value mode | Match |
| --- | --- |
| `"enter"` | False-to-true transition; default |
| `"each_sample"` | Every sample where the condition is true |
| `"change"` | Any change in the Boolean condition |

Value and comparisons support arbitrary-width unsigned XData. ValueChange compares the full aval/bval representation.

## Timeouts

SimTimeout measures simulation cycles; WallTimeout measures seconds. Both return
`XEventKind.TIMEOUT` rather than raising automatically. Handle timeout explicitly when it should fail a test:

```python
from toffee import AnyOf, RisingEdge, SimTimeout, Value, XEventKind

event = await AnyOf(
    Value(dut.result, wanted, sample=RisingEdge(clock)),
    SimTimeout(100, clock=clock),
)
if event.kind is XEventKind.TIMEOUT:
    raise TimeoutError("result was not observed within 100 cycles")
assert event.value == wanted
```

Provide clocks, signals, expectations, and budgets from the test. By the time a combined wait resumes,
simulation may have advanced. Use event values or synchronous capture snapshots instead of rereading live signals.
An outer `asyncio.timeout(seconds)` can bound the entire test's wall time.

## asyncio sources

```python
await AsyncioEventTrigger(stop_event)
item = (await QueueTrigger(queue)).value
result = (await TaskComplete(task)).value
```

## Combine waits

```python
event = await AnyOf(
    Value(dut.done, 1, sample=RisingEdge(dut.clock)),
    AsyncioEventTrigger(stop_event),
    WallTimeout(1.0),
)
if event.kind is XEventKind.TIMEOUT:
    raise TimeoutError("neither DUT completion nor stop event arrived")

event = await AllOf(
    ClockCycles(dut.clock, 10),
    TaskComplete(reference_task),
)
```

AnyOf selects a completed source and cancels the others. AllOf waits for every source and records child events in `event.causes`.
If several sources are already complete when the waiter resumes, AnyOf chooses argument order, not event tick order.
It does not drain all pending notifications in a simulation phase before choosing.
Therefore generic timeout races do not enforce exact transaction deadlines. Use
[Scoreboard response budgets](scoreboard.md) to accept deadline-cycle responses and reject late ones reliably.

Wrap an existing task in `TaskComplete(task)` to produce an XEvent and protect the original task.
Passing a task directly lets AnyOf cancel a losing task, and lets AllOf cancel unfinished sources during failure cleanup.
See [asyncio ownership](asyncio-and-pytest.md).
