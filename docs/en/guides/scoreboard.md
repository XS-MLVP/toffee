# Scoreboards and pipelined transactions

Scoreboard.check compares supplied values. Its asynchronous mode consumes acceptance and observation streams,
associates records, and reuses the same comparison. Handshake, reset, and flush rules remain project-defined.

## Choose an operation

| Goal | API | Completion |
| --- | --- | --- |
| Compare available values | `check(expected=..., actual=...)` | Immediate comparison without binding |
| Wait for input acceptance | `await driver.send(request)` | Acceptance event |
| Submit input only | `async_driver.send(request)` | Immediate handle completed at acceptance |
| Submit and check response | `scoreboard.submit(request)` | Immediate XTransfer completed by checker |
| Own response processing | `driver.submit(request)` | Caller associates and completes handle |
| Wait for one response | `await transfer` from submit | Checked response or failure |
| Finish a batch, allow another | `drain(timeout_cycles=...)` | Work submitted before the call |
| Finish submissions and check | `finish(timeout_cycles=..., observe_cycles=...)` | Drain, finite observation, residual checks |
| Exit or clean up | context / `aclose()` | Check current state and release resources; no DUT drain |

Transfer is an observation; XTransfer tracks acceptance/completion.
SyncDriver.send runs in its caller; AsyncDriver.send and submit immediately submit input.
These are AsyncDriver guarantees, not universal requirements on Driver/SignalDriver subclasses.
Input-only handles complete at acceptance without a fabricated response or recv_accepted entry.
If DUT responses require checking, use scoreboard.submit. send does not suppress real output or hide unmatched observations.

## Compare directly

```python
from toffee import Scoreboard

scoreboard = Scoreboard("prediction")
scoreboard.check(expected=wanted, actual=prediction)
```

Default comparison recursively handles dataclasses, mappings, sequences, BundleValue, and scalars.
ScoreboardMismatch derives from AssertionError and includes field paths, expected/actual values, and context.
Differences are computed once, retaining at most `max_differences=20` by default.
Supply `compare(expected, actual)` or override compare for custom rules; custom assertions remain the exception cause.

## Bind a response path

```python
async with (
    Execution(backend) as execution,
    prediction_driver as driver,
    Scoreboard(
        "prediction", expected=lambda request, _: model.predict(request),
    ).bind(
        execution, driver=driver, monitor=prediction_monitor,
        clock=clock, response_timeout_cycles=20,
    ) as scoreboard,
):
    for request in requests:
        scoreboard.submit(request)
    status = await scoreboard.finish(timeout_cycles=200, observe_cycles=2)
```

Run inside an active [Execution](execution-and-backend.md). Pass clock and response_timeout_cycles explicitly.
A Scoreboard binds once. The Driver supplies submit/recv_accepted and the Monitor supplies start/recv/aclose.
Scoreboard exclusively consumes and normally manages the Monitor; the outer scope manages Driver lifetime.
An [Agent](agents-and-reference-models.md) performs this wiring internally.
With `manage_monitor=False`, the outer scope starts the Monitor first and closes it after Scoreboard;
consumption remains exclusive, and two Scoreboards cannot share the stream.

Monitoring starts on context entry or first submission and stays active across batches until aclose.
Matching the final transaction neither stops monitoring nor invokes Driver idle/reset operations.
Without a context, close in finally.
Acceptance and observation events must be rising-stable events of the specified single XClock.
Deliver captured records promptly, without waiting for unrelated HTTP, wall delay, or future simulation events.
Budgets use half-tick timestamps. At the falling barrier following the deadline's rising phase, the checker
pauses progress and drains ready source consumers, so correct deadline-cycle responses beat timeout.

## Predict and await responses

```python
# 向量给出预期值；优先于 expected source。
transfer = scoreboard.submit(request, expected=wanted)

# 只有控制流需要结果时才等待；逐次等待会使激励串行化。
actual = await transfer
```

The expected source runs when acceptance records are consumed, in notification order.
CheckContext.accepted_event retains the real acceptance time. Missing both explicit expected and a source raises MissingExpectedError.
Do not mutate requests or expectations after submission.
The predictor must be synchronous. Explicit expected skips this standalone predictor, so do not mix that shortcut
with a predictor used as a stateful model. Agent.connect advances its model independently of overrides.

Transactions progress PENDING → PROCESSING → COMPLETED, or FAILED on error.
Only PENDING work can be cancelled. Cancelling a waiter neither cancels the transaction nor observes its eventual failure.
Abnormal close marks accepted unfinished transactions FAILED, not CANCELLED.

## Associate observations

- FIFO matches valid responses in acceptance order.
- request_key/response_key permits reordering. Duplicate active keys raise ScoreboardAssociationError.
- `latency_cycles=N` matches exactly N cycles after acceptance, including N=0. Missing that window times out immediately; N must not exceed the response budget.

By default each observation is valid output; extra, duplicate, or unassociated output fails strict finish.
For a Monitor sampling every cycle including idle, use `latency_cycles=N, sampled=True` to ignore samples outside target windows.
sampled requires fixed latency and cannot combine with key matching.
A custom synchronous, side-effect-free `associate(observation, pending)` returns an entry or None to buffer the observation.
It does not run models, compare, or await events, and can handle Python observation delivery before the matching acceptance notification.

## Budgets and cleanup

| Budget | Starts at | Covers |
| --- | --- | --- |
| response_timeout_cycles | Input acceptance | Response; fixed latency uses its earlier exact window |
| drain/finish timeout_cycles | Method call | Queued/unaccepted work and draining; finish also includes observation |
| asyncio.timeout(seconds) | Context entry | Caller-selected wall time |

`drain` captures a submission watermark; later submissions do not extend that wait.
`finish` closes admission, drains, observes M cycles (default 0), and checks conservation.
Its total budget includes observation; no later submissions are allowed.
`aclose` processes ready notifications, checks state, and releases resources without waiting for the DUT.
Unfinished requests raise ScoreboardIncompleteError; unmatched output raises ScoreboardAssociationError.

## Diagnose timeout and background failure

ScoreboardTimeoutError derives from both ScoreboardError and TimeoutError.
Inspect structured attributes instead of parsing messages:

| Attribute | Meaning |
| --- | --- |
| name, operation | Checker name; response / fixed_latency / drain / finish |
| budget_cycles, elapsed_cycles | Effective budget and elapsed time, possibly fractional |
| start_tick, deadline_tick, current_tick | Half-tick timestamps; a late response uses observation time |
| context | Response CheckContext with sequence_id, request, accepted_event; None for overall budgets |
| status | Diagnostic counter snapshot |
| reason | Deadline exceeded or insufficient remaining time for final observation |

Acceptance at tick 20 with a four-cycle budget sets tick 28 as deadline. Detection at falling tick 29
reports 4.5 elapsed cycles. An observation window that cannot fit the remaining budget fails immediately,
possibly before elapsed time reaches the budget.
Errors reach pytest through transfer waits, drain, finish, or close; an already observed error is not repeated.
Independent cleanup errors are grouped with the original exception.
`capacity=64` separately bounds active requests and unmatched observations; overflow fails explicitly.
Completed entries are removed while counters persist. Keep full request history separately if needed.
ScoreboardStatus is diagnostic, not a pytest verdict or coverage result.

Run the MemoryBackend example without RTL:

```bash
python3 examples/transactions/scoreboard.py
```

See [source](https://github.com/XS-MLVP/toffee/blob/main/examples/transactions/scoreboard.py)
and [pipeline scenarios](pipeline-examples.md).
