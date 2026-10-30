# Integrate asyncio and pytest

Toffee uses the running asyncio loop alongside HTTP clients, servers, queues, timers, and other tasks.
This guide focuses on host integration and component authors. Ordinary verification uses Driver submissions,
Monitor/Scoreboard lifecycles, and Trigger waits; [pipelines](pipeline-examples.md) need no manually created concurrency tasks.
There is no generic parallel interface. See the [execution model](../explanation/execution-model.md) for scheduling and ownership.

## pytest

```python
import pytest

from toffee import Execution, RisingEdge


@pytest.mark.asyncio
async def test_counter(backend, dut):
    async with Execution(backend):
        await RisingEdge(dut.clock)
        assert int(dut.count.U()) == 1
```

pytest-asyncio provides the loop. Use await directly; see the [fixture tutorial](../getting-started/reference-model.md) for complete tests.

## Task ownership

Execution starts and stops registered Agents. AsyncDriver owns input tasks, Scoreboard owns response collection,
and each Monitor has one lifecycle owner. Tasks or TaskGroups created by external libraries remain owned by those libraries;
scheduling observation does not transfer cleanup responsibility.

AsyncDriver.send submits immediately and returns XTransfer, which is awaitable but is not a coroutine.
Do not pass it to create_task or TaskGroup.create_task. Save the handle and await it where needed.
This does not change SyncDriver or protocol-specific interfaces that return coroutines.

## External services

Tasks created inside Execution normally inherit its scheduling domain.
Use `execution.external_task(coro, name=None)` for independent services or models:

```python
async with Execution(backend) as execution:
    task = execution.external_task(fetch_configuration(), name="configuration")
    configuration = await task
    await FallingEdge(clock)
    apply_configuration(configuration)
```

It returns a native Task, retains user ContextVars, and clears simulation ownership and the implicit Reactor binding.
Such a task must deliver data back to simulation tasks instead of awaiting XTrigger or implicitly driving the DUT.
Normally inherited child tasks remain external. An existing task cannot be reclassified retrospectively.
Direct awaiting retains asyncio cancellation propagation. The creator must still clean up the task.
To freeze clocks while obtaining configuration:

```python
async with execution.paused():
    configuration = await execution.external_task(fetch_configuration())
```

External work does not count toward simulation barriers, but shares the loop's observation hooks and their overhead.
See [limitations](../reference/semantics-and-limitations.md).

## Await external work

An ordinary coroutine can be awaited directly:

```python
response = await http_client.get(url)
await RisingEdge(dut.clock)
```

Combine DUT and external sources with AnyOf:

```python
event = await AnyOf(
    Value(dut.done, 1, sample=RisingEdge(dut.clock)),
    QueueTrigger(control_queue),
    WallTimeout(2.0),
)
if event.kind is XEventKind.TIMEOUT:
    raise TimeoutError("no DUT result or control message within 2 seconds")
```

Simulation advances while DUT Triggers or subscriptions exist. Waiting only for HTTP, a Queue, or a plain Task
with no simulation watchers leaves simulation time unchanged. Persistent Monitors/Scoreboards are watchers,
so the clock can continue while the caller awaits HTTP; pause explicitly when required.

## Protect an existing task

TaskComplete wraps results as XEvent and shields the original task:

```python
from toffee import AnyOf, TaskComplete, WallTimeout, XEventKind

# reference_task 由外层创建并负责最终 await 或取消。
event = await AnyOf(TaskComplete(reference_task), WallTimeout(2.0))
if event.kind is XEventKind.TIMEOUT:
    raise TimeoutError("reference task did not complete within 2 seconds")
result = event.value
```

Timeout or cancellation of the wait does not cancel `reference_task`; its owner still performs cleanup.
Direct `AnyOf(reference_task, ...)` cancels it when it loses.
Likewise, cancelling a waiter on XTransfer does not cancel the transaction.
Only an unaccepted request can be explicitly cancelled with `transfer.cancel()`; see [Scoreboard](scoreboard.md).

## Background task cleanup

```python
import asyncio
import contextlib

task = asyncio.create_task(run_reference_server())
try:
    async with Execution(backend):
        ...
finally:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
```

Execution cleans up its own pump, subscriptions, and registered Agents. Creators close independent background tasks.

## Worker threads

Use `asyncio.to_thread()` for blocking functions. Access Backend, XClock, and DUT signals from the Execution thread.
Deliver worker results through asyncio queues or events.
