# 8. Submit pipelines, multiple batches, and cancellations

Keep inputs moving while earlier requests wait for responses. Inputs take one cycle to accept and
responses arrive two cycles later, allowing several transactions to be outstanding.

## Distinguish the waits

Repeated `await agent.send(...)` already waits only for acceptance, not each response.
`submit()` instead queues work immediately and returns a response handle:

| Operation | Completion means | Driver requirement |
| --- | --- | --- |
| `await agent.send(3)` | Input accepted; connected response checking continues | Sync or Async |
| `transfer = agent.submit(3)` | Handle returned immediately; acceptance is not guaranteed yet | Async |
| `result = await transfer` | This response arrived and passed checking | Handle from submit |

Waiting for a handle again retrieves that transaction's result; it does not resend input.
An Agent with a Sync Driver does not support `submit()`.

## Keep timing, change submission

Continue in `examples/getting_started/test_accumulator.py`:

<!-- executable-example: examples/getting_started/test_accumulator.py#pipeline_setup -->
```python
from toffee import AsyncSingleCycleDriver


def make_pipeline_agent(dut, *, response_timeout_cycles=3):
    driver = AsyncSingleCycleDriver(
        dut.clock,
        Bundle(enable=dut.enable, operand=dut.operand),
        idle={"enable": 0, "operand": 0},
        encoder=encode_operand,
    )
    return Agent(
        "accumulator",
        driver=driver,
        monitors={"response": make_response_monitor(dut)},
        response_monitor="response",
        clock=dut.clock,
        response_timeout_cycles=response_timeout_cycles,
    )
```

`AsyncSingleCycleDriver` reuses the same Bundle, encoder, idle values, and Monitor.
It drives queued requests in order, without the test managing tasks, locks, or `parallel`.
Async does not permit simultaneous writes to the same signals: this interface still accepts one input per cycle.

## Send three consecutive requests

<!-- executable-example: examples/getting_started/test_accumulator.py#pipeline -->
```python
@pytest.mark.asyncio
async def test_three_inputs_in_flight():
    dut = TutorialDut(latency=2)
    agent = make_pipeline_agent(dut)
    agent.connect(AccumulatorModel())
    try:
        async with Execution(dut.backend, agents=[agent]):
            first = agent.submit(3)
            second = agent.submit(5)
            third = agent.submit(7)

            await agent.finish(timeout_cycles=10, observe_cycles=1)
            assert await first == 3
            assert await second == 8
            assert await third == 15
            assert dut.accepted_cycles == [1, 2, 3]
            assert dut.response_cycles == [3, 4, 5]
    finally:
        dut.close()
```

```bash
python3 -m pytest -q examples/getting_started/test_accumulator.py::test_three_inputs_in_flight
```

Expect `1 passed`. The test checks this timing:

| Rising edge | Newly accepted operand | Response | Outstanding requests |
| --- | --- | --- | --- |
| 1 | 3 | None | 1 |
| 2 | 5 | None | 1, 2 |
| 3 | 7 | 3 | 2, 3 |
| 4 | None | 8 | 3 |
| 5 | None | 15 | None |

`assert await first == 3` retrieves and checks the first request's result. These assertions appear
after finish to show the handle results; finish reports background failures even if handles are not awaited.

Try inserting `assert await first == 3` immediately after the first submit. The second request then
waits for the first response, so the consecutive acceptance assertion `[1, 2, 3]` fails.
Restore the example. Waiting for every response after submitting serializes stimulus regardless of Driver type.

## Reuse one environment for two batches

<!-- executable-example: examples/getting_started/test_accumulator.py#batches -->
```python
@pytest.mark.asyncio
async def test_two_batches_and_withdrawn_input():
    dut = TutorialDut()
    agent = make_pipeline_agent(dut)
    agent.connect(AccumulatorModel())
    try:
        async with Execution(dut.backend, agents=[agent]):
            first = agent.submit(3)
            withdrawn = agent.submit(99)
            withdrawn.cancel()
            await agent.drain(timeout_cycles=10)
            assert await first == 3

            second = agent.submit(5)
            await agent.finish(timeout_cycles=10, observe_cycles=1)
            assert await second == 8
            assert dut.operands == [3, 5]
    finally:
        dut.close()
```

```bash
python3 -m pytest -q examples/getting_started/test_accumulator.py::test_two_batches_and_withdrawn_input
```

Expect `1 passed`. The queued 99 is cancelled before acceptance, so it reaches neither DUT nor model.
`drain()` waits for work submitted before the call and permits a later batch. `finish()` finally closes admission.
The Monitor remains active throughout the environment.
Cancellation here occurs before any await, while the request is still queued. Once accepted, cancellation
raises an error; the response or timeout still needs handling. This does not implement a DUT-specific flush protocol.

Continue with [failure diagnostics](failures.md), or try [out-of-order responses](out-of-order.md).
For multistage inputs, use the [stage overlap examples](../guides/pipeline-examples.md) and define stage
resources explicitly rather than increasing SingleCycle concurrency.
