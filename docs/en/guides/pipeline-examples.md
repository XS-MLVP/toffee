# Pipelined input and overlapping stages: runnable examples

This page distinguishes consecutive input submissions, overlapping input-driving processes,
and multiple outstanding responses. The examples use AsyncDriver: send tracks input acceptance,
while submit tracks the later response. Both submit immediately when called and return awaitable
handles. These are this template's capabilities, not requirements for every Driver. SyncDriver
still drives through await send; protocol Drivers define their own send/completion contracts.

Concrete `_drive_one()` methods define all input timing without inheriting a single-cycle class.
The examples neither use ready/valid or reset/flush nor infer a DUT protocol.

## Run and select scenarios

From the repository root:

```bash
python3 -m pip install -e '.[test]'
python3 examples/transactions/pipeline.py
```

The default runs six successful scenarios, printing acceptance, responses, and cycle samples.
[pipeline.py](https://github.com/XS-MLVP/toffee/blob/main/examples/transactions/pipeline.py) contains
the runnable substitute, Drivers, Monitor, Scoreboard wiring, and cleanup.

| Behavior | Individual argument | Mechanism |
| --- | --- | --- |
| One input each cycle while earlier responses remain outstanding | `--case continuous` | submit, max_active=1, fixed latency |
| Ordered responses with variable latency | `--case fifo` | Default FIFO association |
| Complete inputs traverse two stages serially | `--case serial` | max_active=1 |
| Different requests occupy separate stages | `--case overlap` | max_active=2, per-stage locks |
| Submit each request only after previous acceptance | `--case send` | Sequential await send, serial even at max_active=2 |
| Responses return in reverse order | `--case out-of-order` | request_key / response_key |

For example:

```bash
python3 examples/transactions/pipeline.py --case overlap
```

With native bindings, the same code runs on real XClock:

```bash
python3 examples/transactions/pipeline.py --backend native
```

This tests native scheduling without RTL. If bindings are in a build directory, configure
PYTHONPATH as described in [installation](../getting-started/installation.md); pytest's local-build
autodiscovery does not apply to ordinary scripts.

## Explicit substitute behavior

ToyPipeline samples actually driven signals at every rising edge, rather than treating Python
Driver logs as observations. Its conventions are:

- Positive values 1, 2, 3 identify requests; 0 is this substitute's idle value.
- A positive ordinary input is accepted and produces `Response(tag=request, value=request * 10)`.
- For staged input, stage one occupies one cycle and stage two occupies three. Complete input
  acceptance occurs at stage two's third sample.
- Printed ticks are half ticks. Starting at zero, rising edges occur at 2, 4, 6, and so on.

The substitute and concrete Drivers define these rules explicitly. Replace them with your design's
acceptance point, output sampling, and idle policy when integrating a real project.

## 1. Continuous input without waiting for earlier responses

Input directly subclasses AsyncDriver with `max_active=1`:

```python
class Input(AsyncDriver[int, Response]):
    def __init__(self, toy, *, capacity=8):
        self.clock, self.data = toy.clock, toy.first
        super().__init__((self.data,), name="input", capacity=capacity, max_active=1)

    async def _drive_one(self, request):
        self.data.Set(request)
        try:
            return await ClockCycles(self.clock, 1)
        finally:
            self.data.Set(0)
```

Each `_drive_one()` handles input acceptance only; Monitor and Scoreboard track the response.
`run_responses()` submits every input before waiting for completion:

```python
transfers = [board.submit(request) for request in (1, 2, 3)]
status = await board.finish(timeout_cycles=30, observe_cycles=1)
values = [await transfer for transfer in transfers]
```

Do not await each `board.submit(request)` inside the submission loop: that would delay each new
input until the previous response. Here, finish drains transactions before handles are read.

The fixed-latency case explicitly sets `latency_cycles=3`:

| Cycle | 1 | 2 | 3 | 4 | 5 | 6 |
| --- | --- | --- | --- | --- | --- | --- |
| Input request | 1 | 2 | 3 | — | — | — |
| Response tag | — | — | — | 1 | 2 | 3 |

Output:

```text
accepted=[(1, 2), (2, 4), (3, 6)]
responses (tag, tick): [(1, 8), (2, 10), (3, 12)]
```

The third input is accepted before the first response returns. Thus max_active=1 does not limit
the design to one outstanding response. The generic serial worker reuses its driving coroutine;
these consecutive inputs have no extra idle cycle.

## 2. Two input stages: serial and overlapping

QueuedStages subclasses AsyncDriver with the following driving function. Separate stage locks
prevent different requests from writing the same signals simultaneously:

```python
async def drive_stages(driver, request):
    async with driver.resource_lock("first"):
        driver.first.Set(request)
        try:
            await ClockCycles(driver.clock, 1)
        finally:
            driver.first.Set(0)
    async with driver.resource_lock("second"):
        driver.second.Set(request)
        try:
            return await ClockCycles(driver.clock, driver.second_cycles)
        finally:
            driver.second.Set(0)
```

`resource_lock("first")` protects only stage one; stage two has its own lock. A single lock around
the whole function would serialize complete inputs.

Resource locks are reused by name within a Driver. The Driver declares resource scope; tests
simply submit inputs. Execution settles ready callbacks and cascading wakeups before advancing
or sampling DriveStable, so stage code needs no added sleep(0). Locks do not specify occupied
cycles or handoff timing; explicit waits and signal operations do.

Compare:

```bash
python3 examples/transactions/pipeline.py --case serial
python3 examples/transactions/pipeline.py --case overlap
```

| max_active | Request 1 acceptance cycle | Request 2 | Request 3 |
| --- | --- | --- | --- |
| 1 | 4 | 8 | 12 |
| 2 | 4 | 7 | 10 |

Actual samples in overlap mode:

| Cycle | Stage one signal | Stage two signal | Complete input acceptance |
| --- | --- | --- | --- |
| 1 | 1 | 0 | — |
| 2 | 2 | 1 | — |
| 3 | 0 | 1 | — |
| 4 | 0 | 1 | Request 1 |
| 5 | 3 | 2 | — |
| 6 | 0 | 2 | — |
| 7 | 0 | 2 | Request 2 |

At cycle 2, request 2 occupies stage one while request 1 occupies stage two: actual sampled overlap.
Stage two's three-cycle occupancy limits sustained throughput. Increasing max_active does not
make every stage accept once per cycle.

These cases demonstrate input acceptance only, using send without response-bearing transactions:

```python
async with Execution(toy.backend), QueuedStages(toy, max_active=2) as driver:
    inputs = [driver.send(request) for request in (1, 2, 3)]
    accepted = [await item for item in inputs]
```

All inputs submit first, and the Driver schedules them by max_active and stage resources. Reading
handles in order afterward does not serialize already-submitted work. At acceptance, a handle
becomes COMPLETED; its result, accepted_event, and completed_event are the same event. Capacity
is released, and the input does not enter the recv_accepted response-association stream. Multiple
batches can share one Driver context.

Inputs execute even without waiters. Repeatedly awaiting one handle retrieves its result without
resending. Full capacity or an unstarted Driver fails directly at send. To prepare data for later,
store requests and call send only when submission is intended.

Explicit `item.cancel()` can cancel unaccepted input. Cancelling a waiter does not retract it.
Driving errors propagate through the handle or Driver context exit; already-observed errors are
not duplicated. Context exit cancels unaccepted inputs and reclaims workers without implicitly
waiting for every submission. Await required handles before exit. Response checking owns
completion for submit transactions.

## 3. Sequential waits versus batch submission

```bash
python3 examples/transactions/pipeline.py --case send
```

Even with max_active=2, this QueuedStages loop sends requests sequentially:

```python
for request in (1, 2, 3):
    await driver.send(request)
```

Each new request submits after the previous acceptance, giving ticks 8, 16, 24. Submitting first
and awaiting later gives 8, 14, 20. The Driver manages stage concurrency; callers need not create tasks.

SyncDriver remains suitable for callers directly awaiting input acceptance and has no background
submission queue. Choose a Driver matching the project's timing and lifecycle.

## 4. FIFO, fixed latency, and key association

Only bind's association configuration changes across these `run_responses()` cases:

| Mode | Association arguments | Actual response latencies |
| --- | --- | --- |
| continuous | `latency_cycles=3` | Three cycles each |
| fifo | None | 3, 3, 4 cycles, in order |
| out-of-order | request_key / response_key | 5, 3, 1 cycles |

Every mode explicitly sets clock and response_timeout_cycles. Fixed-latency mode checks the target
cycle proactively, rather than merely matching a response if it eventually arrives.

Out-of-order configuration:

```python
board.bind(
    execution, driver=driver, monitor=monitor, clock=clock,
    response_timeout_cycles=6,
    request_key=lambda request: request,
    response_key=lambda response: response.tag,
)
```

Requests accept in order 1, 2, 3, while responses arrive 3, 2, 1:

```text
accepted=[(1, 2), (2, 4), (3, 6)]
responses (tag, tick): [(3, 8), (2, 10), (1, 12)]
```

Each XTransfer receives its own tag's response. Reading handles in submission order after submitting
all requests does not change completion order. Outstanding keys must be unique; duplicate tags
cannot provide unambiguous association.

## 5. Failure demonstrations

These commands **are expected to exit nonzero and report exceptions**. They are excluded from
the default successful scenarios:

```bash
python3 examples/transactions/pipeline.py --case missing
python3 examples/transactions/pipeline.py --case late
python3 examples/transactions/pipeline.py --case wrong-fifo
python3 examples/transactions/pipeline.py --case capacity
```

| Case | Deliberate setup | Expected failure |
| --- | --- | --- |
| missing | Accepted request never responds; two-cycle budget | ScoreboardTimeoutError, operation=response |
| late | Required latency 3, actual response scheduled at 4 | ScoreboardTimeoutError, operation=fixed_latency, immediately on missing the target window |
| wrong-fifo | Reverse response order with FIFO association | ScoreboardMismatch: expected tag 1, received tag 3 |
| capacity | Submit three requests with Driver capacity 2 | RuntimeError: submission capacity is full |

Failure paths still exit Scoreboard, Driver, and Execution and close the Backend. Ignoring transfer
awaits cannot hide background errors: finish or context exit propagates them.

## Keep parameters distinct

| Parameter | What it limits | Example |
| --- | --- | --- |
| `max_active` | Concurrent `_drive_one()` input processes | 1 for continuous input, 2 for overlapping stages |
| Driver `capacity` | Submitted but unfinished work: queued, driving, or awaiting response | Default 8; full submission raises instead of waiting |
| Scoreboard `capacity` | Active transactions and unmatched observations | Default 64, checked independently of Driver capacity |
| `latency_cycles` | Fixed acceptance-to-response window | 3 for continuous |
| `response_timeout_cycles` | Response budget after acceptance | 6 for successful cases; an earlier fixed-latency window still applies |
| `finish(timeout_cycles=...)` | Total drain and trailing-observation budget | 30, including observe_cycles=1 |

Capacity is not pipeline depth, and max_active is not the maximum outstanding response count.
See [Scoreboard](scoreboard.md) for cancellation and completion semantics.

## Automated verification

```bash
python3 -m pytest -q tests/integration/test_pipeline_examples.py --require-xspcomm
```

MemoryBackend and native XClock regressions check bubble-free input, three outstanding responses,
success at a fixed-latency deadline, sampled timings at max_active=1/2 for both example Drivers,
stage exclusion, variable-latency FIFO, reverse key association, and all four expected failures.
They also check tasks, watchers, backend leases, idle signals, and native callback cleanup.

`tests/integration/test_driver_handoffs.py` additionally checks cascading lock handoffs without
manual yields, writes before DriveStable, concurrency-slot handoff, fairness, cancellation, errors,
and external tasks not blocking simulation.

Tests reuse these Drivers and substitutes instead of maintaining approximations. Wall-clock
timeouts only prevent hangs; missing/late responses must fail through simulated cycle budgets.
