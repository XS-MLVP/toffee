# Simulation scheduling and resource ownership

Toffee has two kinds of progress: Python tasks execute on asyncio, and the DUT advances on a
simulation clock. Their relationship explains why the DUT may keep running during an HTTP wait
and why ending a test does not automatically destroy the DUT.

## Ready Python work and clock advancement

asyncio is the sole scheduler of Python tasks and callbacks. Execution observes immediate callbacks
belonging to this simulation. It allows the next clock advancement or DriveStable sampling only
after those callbacks and their cascading wakeups finish. This is phase settling. For example,
a response wakes a checker, which releases input capacity, allowing a Driver to submit another
input. That immediate chain must finish before the clock moves.

The Backend advances simulation and returns hit events. Supported compiled conditions evaluate
natively, reducing Python scheduling on cycles without hits. Drivers, Monitors, or `@pytrigger`
functions that execute Python every cycle still incur those calls, so batching benefits depend
on the actual test structure.

Waiting for an incomplete external Future is not ready simulation work. With a DUT trigger or
persistent subscription, the clock may continue; without simulation watchers, waiting only for
HTTP or a Queue does not advance time. Persistent Monitors usually retain watchers, so a test
waiting for configuration does not imply a paused DUT.

`Execution.paused()` explicitly freezes simulation time while host asyncio continues. Awaiting
a trigger that requires clock progress during a pause cannot complete. See
[external asyncio integration](../guides/asyncio-and-pytest.md).

## Three budgets constrain different work

| Budget | What it limits | What it does not establish |
| --- | --- | --- |
| Transaction or finish cycle budget | Simulated response, drain, and trailing observation cycles | Completion within a specified number of real seconds |
| Wall-clock timeout | Real elapsed time, including external I/O | Compliance with DUT cycle latency |
| `max_settle_rounds` | Yields back to asyncio while settling one phase | Number of simulated cycles waited |

An in-domain task looping forever on `await asyncio.sleep(0)` keeps ready work in the current
phase. `SimulationNotSettledError` diagnoses this; the clock does not skip that work. Raise the
budget only for finite but long chains. Move unrelated services outside the domain using
`external_task`. See [Execution reference](../reference/execution.md).

## Scheduling membership and lifecycle ownership

A task belonging to the simulation scheduling domain does not automatically transfer its lifetime
to Execution. Execution starts/stops explicitly registered Agents, and Agents manage their Drivers
and Monitors. Creators still own independently created background tasks. `external_task` removes
the implicit Reactor binding and runs a service outside the domain; it does not clean up that service.

| Resource | Lifecycle owner |
| --- | --- |
| Current test's waits, subscriptions, compiled state, and advancement task | Execution |
| Registered Agents and their components | Execution and Agent component management |
| Backend | Creator or fixture, calling `close()` |
| DUT | Project creator or fixture, including initialization and release |
| Independent external task | Creator, awaiting completion or cancelling and joining |

This boundary permits sequential Backend reuse across tests without implicitly resetting the DUT.
Projects needing a fresh state must define a reset sequence. Previous Execution exit does not
mean the design returned to its initial state.

## One owner advances a clock

Inside Execution, `XCommClockBackend` exclusively controls its XClock. Calling `dut.Step()` as well
would create two advancement paths without one sampling order, so it is rejected. Pausing retains
that ownership; manual advancement becomes available only after exit.

Ownership covers that clock domain only. Multiple Executions do not automatically share one
timeline. See [Execution reference](../reference/execution.md) for manual-step restrictions and
[Execution configuration](../guides/execution-and-backend.md) for fixture examples.
