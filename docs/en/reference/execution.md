# Execution reference

Import with `from toffee import Execution`. For setup, see
[Execution and Backend](../guides/execution-and-backend.md); for rationale, see
[scheduling and resource ownership](../explanation/execution-model.md).

## Constructor parameters

`backend` is required and accepts positional or keyword use. All other parameters are keyword-only.

| Parameter | Default | Meaning and constraints |
| --- | --- | --- |
| `backend` | Required | SimulationBackend supporting half-step and stable sampling |
| `agents` | `()` | Iterable of distinct Agent instances; Agents cannot share components |
| `coverage` | `()` | Iterable of distinct CoverGroups whose sampling Execution starts/stops |
| `default_sample` | `None` | Default condition sampling phase; must be RisingEdge, FallingEdge, or DriveStable |
| `max_batch_ticks` | `4096` | Maximum half ticks per Backend call; positive |
| `quantum_ms` | `10.0` | Wall-time budget for continuous advancement, in milliseconds; positive |
| `budget_check_interval` | `64` | Native executor's time-budget checking interval; positive |
| `max_settle_rounds` | `100_000` | Maximum asyncio yields while settling one phase; positive integer, not bool |

The three advancement budgets do not replace test timeouts or transaction response budgets.
`max_settle_rounds` is not a cycle count. Without `default_sample`, conditions requiring a sampling
phase must pass `sample` explicitly; see [triggers](../guides/triggers.md).

## Scope and methods

| Entry point | Contract |
| --- | --- |
| `async with Execution(...) as execution` | Acquire Backend control and start registered coverage/Agents; clean current resources and release control on exit |
| `execution.paused()` | Async context manager freezing simulation while host asyncio runs; resume after all nested pauses exit |
| `execution.external_task(coro, *, name=None)` | Create an out-of-domain native Task in the active Execution; retain user ContextVars and clear the implicit Reactor binding |
| `execution.subscribe(spec)` | Register a bound specification in the active Execution and return a Subscription |

Normal exit, test exceptions, and startup failure all clean resources. Agents close in reverse
order, covergroups synchronize and stop, and current waits, subscriptions, and compiled state
are released. Execution does not close the Backend, reset the DUT, or terminate independently
created background tasks. Creators own `backend.close()`, DUT release, and external-task cleanup.

A Backend can belong to only one active Execution. Reusing it does not reset clock or DUT state.
Create fresh instances of closed Agents and built-in Monitors.

## XClock ownership

| Period | Manual advancement |
| --- | --- |
| Backend constructed, before Execution entry | Control not acquired; `dut.Step(...)` can initialize the DUT |
| Inside Execution, including pauses | Native XEngine exclusively owns XClock; manual advancement and reset are prohibited |
| After Execution exits and releases control | Manual control resumes; release itself does not advance or reset the clock |

Inside the scope, `dut.Step`, `clock.Step`, `RawStep`, `StepHalf`, and `Reset` raise errors.
Legacy `AStep`, `ANext`, `ACondition`, and `RunStep` also cannot be mixed in. Separate Backends
wrapping the same XClock cannot take control simultaneously.

If exit occurs halfway through a cycle, finish the remaining half with `StepHalf` before calling
full-cycle `Step`. Calling Backend `arm` / `run_until` directly acquires control on first use and
retains it until `close()`. Remove directly registered watchers before entering Execution.

## Settling and concurrency limits

Exceeding `max_settle_rounds` raises `SimulationNotSettledError` with tick, phase, and unfinished
callbacks. It never skips ready work to advance. Do not await clock-dependent triggers inside a pause.

Backend, XClock, and DUT signals do not support concurrent cross-thread access. Multiple Executions
do not provide a shared timeline. See [semantics and limitations](semantics-and-limitations.md)
for event-loop compatibility and external-event boundaries.
