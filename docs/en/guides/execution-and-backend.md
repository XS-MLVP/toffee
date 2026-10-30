# Configure Execution and Backend

Place an existing DUT within a test scope and release resources afterwards.
These snippets assume a project-provided `dut`, registered `dut.clock`, and compatible xspcomm bindings.
For first-time integration, see [native clocks and project DUTs](../getting-started/native-and-next.md).

## Create and close the backend

Within an async test, create the backend, enter Execution, and close the backend in `finally`:

```python
from toffee import Execution, RisingEdge, XCommClockBackend

backend = XCommClockBackend(dut.GetXClock())
try:
    async with Execution(backend, default_sample=RisingEdge(dut.clock)):
        await RisingEdge(dut.clock)
        # 在稳定上升沿读取并检查 DUT 输出。
finally:
    backend.close()
```

Release the DUT after the backend. Inside Execution use Trigger waits; do not call `dut.Step()` or
run another clock task, even while paused. Execution cleans up its waits and components, while backend and DUT state persist.

## Reuse a backend in pytest

```python
import pytest

from toffee import Execution, XCommClockBackend


@pytest.fixture(scope="session")
def backend(dut):
    backend = XCommClockBackend(dut.GetXClock())
    try:
        yield backend
    finally:
        backend.close()


@pytest.mark.asyncio
async def test_a(backend):
    async with Execution(backend):
        ...


@pytest.mark.asyncio
async def test_b(backend):
    async with Execution(backend):
        ...
```

This requires a session-scoped DUT fixture as well. Match a function-scoped DUT with a function-scoped
backend; see the [fixture tutorial](../getting-started/reference-model.md).
Reusing the backend does not reset the clock or DUT. Run the project's reset sequence after entering Execution when needed.
Construct and connect Agents before registering them with `Execution(backend, agents=[agent])`.
Execution starts components and cleans them up in reverse order. Create fresh Agent instances after exit.
See [Agent wiring](agents-and-reference-models.md).

## Submit inputs and pause explicitly

Execution drains ready simulation work and its cascading wakeups before advancing the clock.
AsyncDriver manages background driving without `sleep(0)` handoffs:

```python
async with Execution(backend), input_driver as driver:
    inputs = [driver.send(request) for request in requests]
    accepted = [await item for item in inputs]
```

Here `input_driver` is an AsyncDriver subclass. Its completion contract depends on the Driver;
see [pipeline examples](pipeline-examples.md).
Draining ready work does not complete external I/O. Explicitly await configuration or initialization.
Use `Execution.paused()` when the clock must remain frozen; do not await a simulation-dependent Trigger
inside that context, since it cannot complete until simulation resumes.

## Tune scheduling only when needed

Start with default budgets. For `SimulationNotSettledError`, inspect tick, phase, and pending callbacks,
including possible endless `sleep(0)` loops. Use `external_task` for independent services as described in
[asyncio integration](asyncio-and-pytest.md). Increase `max_settle_rounds` only for a known finite callback chain.
See [Execution reference](../reference/execution.md) and [execution model](../explanation/execution-model.md).
