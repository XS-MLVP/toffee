# 6. Encapsulate an interface in an Agent

Combine the Driver and Monitor so tests no longer start and consume each component individually.

## Assemble the interface

Continue in `examples/getting_started/test_accumulator.py`, retaining previous definitions:

<!-- executable-example: examples/getting_started/test_accumulator.py#agent_setup -->
```python
from toffee import Agent


def make_agent(dut):
    return Agent(
        "accumulator",
        driver=make_sync_driver(dut),
        monitors={"response": make_response_monitor(dut)},
        response_monitor="response",
        clock=dut.clock,
        response_timeout_cycles=3,
    )
```

`make_agent(dut)` returns an Agent using the factories from the preceding lessons.
The Monitor is named `"response"`; `response_monitor="response"` connects it to the response checker.
Do not also consume that Monitor yourself: you would compete with the checker for observations.

`clock` defines the timeout clock. `response_timeout_cycles=3` allows at most three cycles after each
input is accepted. The specified latency is two cycles; three is an explicit test budget, not a guessed default.
By default, responses match acceptance order (FIFO).

```text
用例 --send(操作数)--> Agent 内的 Driver --> DUT 输入
DUT 响应 --> Agent 内的 Monitor --> 检查器 <-- 用例给出的 expected
```

## Run an automatically checked test

<!-- executable-example: examples/getting_started/test_accumulator.py#agent -->
```python
@pytest.mark.asyncio
async def test_agent_checks_responses():
    dut = TutorialDut()
    agent = make_agent(dut)
    try:
        async with Execution(dut.backend, agents=[agent]):
            await agent.send(3, expected=3)
            await agent.send(5, expected=8)
            await agent.finish(timeout_cycles=10, observe_cycles=1)
    finally:
        dut.close()
```

```bash
python3 -m pytest -q examples/getting_started/test_accumulator.py::test_agent_checks_responses
```

Expect `1 passed`. Constructing an Agent does not start its clock. `Execution(..., agents=[agent])`
starts its components and cleans them up on exit. There is no need for `async with agent` or for tests
to manipulate the internal Driver and Monitor.
`send()` still waits for acceptance, but the connected checker continues checking the later response.

## Finish explicitly

`finish(timeout_cycles=10, observe_cycles=1)` closes admission, waits for submitted requests to finish,
and observes one extra cycle for unexpected responses, within a total budget of ten cycles.
Environment cleanup does not wait indefinitely for unfinished responses.

| Budget | Starts at | Purpose |
| --- | --- | --- |
| `response_timeout_cycles=3` | Each input's acceptance | Deadline for that response |
| `finish(timeout_cycles=10)` | Calling finish | Overall drain and observation budget |
| `observe_cycles=1` | Completion of submitted work | Detect extra output in the observation window |

Choose the observation window from the design's contract. One quiet cycle cannot prove that output will never recur.

## Trigger a mismatch

Change the second send's `expected=8` to 9. Expect `ScoreboardMismatch` with expected 9 and actual 8.
Background checking fails pytest even without a handwritten assertion. Restore 8.
The Agent organizes the interface; the test still supplies expectations. Next, [derive them from a reference model](reference-model.md).
