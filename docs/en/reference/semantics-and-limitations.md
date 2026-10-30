# Semantics and limitations

This page defines current behavioral contracts. Parameters are in [Execution reference](execution.md);
for rationale, see [scheduling and resource ownership](../explanation/execution-model.md).

## Sampling

- RisingEdge and FallingEdge return at stable half steps.
- Value, ValueChange, and compiled conditions evaluate only at their selected sample phase.
- Evaluation begins at the next sample phase after registration.
- The Backend returns events hitting in the same phase as a batch.

## Lifecycle

- Awaiting a trigger creates a one-shot registration.
- Cancelling the wait unregisters its watcher.
- Closing a subscription or exiting Execution unregisters it.
- Execution cleans the current test's triggers and compiled state.
- The caller owns Backend, DUT state, and simulation time.

## Driver submission

- The Driver interface requires an awaitable from send; each implementation defines completion.
- AsyncDriver send/submit submit at the call, not when the caller awaits.
- send returns an input-acceptance handle, automatically completing and releasing capacity at
  acceptance; submit returns a handle waiting for a response.
- Cancelling a waiter does not retract submitted work. Only unaccepted requests can be explicitly cancelled.
- Normal AsyncDriver exit cancels unaccepted inputs without implicitly draining. Await required handles first.
- SyncDriver still returns a driving coroutine without a background queue; protocol and single-cycle
  implementations retain their own timing contracts.

## Agents and transaction checking

- Environments construct Agents and register them through `Execution(agents=[...])`; recreate them after exit.
- connect(ref) does not advance a model. Synchronous ref.accept runs after actual acceptance;
  overriding expected does not skip model updates.
- A checked Agent's send waits only for acceptance while its checker tracks responses. Driver.send's
  independent input-completion contract remains unchanged.
- drain waits for submissions made before the call. finish seals submissions, drains, and observes
  the requested trailing window; it does not close Monitors.
- Responses and acceptance use an explicit single-clock rising-stable phase. Correct responses
  at the deadline take precedence over timeout.
- FIFO/key extra or duplicate responses fail strict end checks. Sampled fixed-latency mode can
  ignore samples outside target windows.
- Unawaited background failures still propagate through finish/exit. Cleanup does not report the
  same already-observed error twice.
- Projects define shared-model ordering across interfaces, passive association, multiple responses,
  and reset/flush behavior; these are not inferred.

## asyncio phase settling

- asyncio alone schedules Python; Execution observes immediate call_soon callbacks without a second queue.
- The current Execution's immediate callbacks and cascading wakeups settle before the next clock
  advancement or DriveStable sample.
- Native Task, Lock, Queue, Event, Semaphore, gather, shield, and TaskGroup retain their semantics.
- An incomplete Future does not block the clock. A new in-domain task's first execution is ready work.
- Timers, I/O, and call_soon_threadsafe are external event entrances. Only in-domain work created
  after delivery joins settling. call_later(0) is not phase-local sleep(0); external completion is
  not promised at a deterministic simulation tick.
- external_task clears the implicit Reactor binding and runs outside the domain; its caller owns cleanup.
- Exceeding max_settle_rounds raises SimulationNotSettledError without skipping work.
- The adapter targets asyncio.BaseEventLoop and has been validated on CPython 3.12's default loop.
  Other implementations need their own adaptation and verification.
- Replacing loop.call_soon during active Execution is unsupported and raises when detected.
  Exit does not overwrite the host's newly installed entry point.
- Multiple Executions share observation infrastructure but settle independently, without a common
  multi-clock timeline.

External tasks still pass through the shared entry point's checks. Logical isolation does not
eliminate scheduling overhead. See [asyncio and pytest](../guides/asyncio-and-pytest.md).

## Data

- Native conditions support arbitrary-width XData with explicit signed or unsigned bit-vector interpretation.
- `bits`, `as_signed()`, `as_unsigned()`, and `resize()` specify types. Two typed operands must match width and signedness.
- Constants must fit the target type; arithmetic results truncate to its width.
- ValueChange compares four-state aval/bval values.
- Data operations use two-state bit-vector semantics, not full SystemVerilog four-state propagation.

See [expressions and resources](../guides/expressions-and-resources.md) for operations, overflow, and division rules.

## Current limitations

- One XCommClockBackend represents one clock domain.
- Ordinary Sequence waits are non-overlapping. Coverage can enable `overlap=True` with a positive
  `max_active`; see [temporal coverage](../guides/coverage-patterns.md).
- `@pytrigger` executes a Python predicate at every sample phase.
- One Backend cannot serve multiple active Executions simultaneously.
- Backend, XClock, and DUT signals do not support concurrent cross-thread access.
- Bundle and channel views require explicit binding; there is no generic Interface base class.
- Coverage JSON uses Toffee's schema, not UCIS.
