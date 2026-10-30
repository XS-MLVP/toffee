# 5. Capture an output that appears two cycles later

Distinguish the signal visible now from a result sampled earlier.
Real interfaces often expose a response for only one cycle.

## Complete the response specification

`TutorialDut(latency=2)` produces its response two cycles after acceptance.
An integer, including 0, means a response is present; `None` means no response.
This Python property is a teaching interface. Real DUT sampling must follow the actual ports and validity rules.

| Rising edge | Input | response |
| --- | --- | --- |
| 1 | Accept 3 | None |
| 2 | Idle | None |
| 3 | Idle | 3 |
| 4 | Idle | None |

Reading `dut.response` at edge 4 misses 3. A Monitor stores it at edge 3.

## Specify when and what to sample

Continue in `examples/getting_started/test_accumulator.py`:

<!-- executable-example: examples/getting_started/test_accumulator.py#monitor_setup -->
```python
from toffee import PythonPredicateTrigger, RisingEdge, SamplingMonitor


def make_response_monitor(dut):
    def response_present():
        return dut.response is not None

    def capture(event):
        return dut.response

    return SamplingMonitor(
        PythonPredicateTrigger(
            "response-present", response_present,
            sample=RisingEdge(dut.clock), mode="each_sample",
        ),
        capture=capture,
    )
```

`response_present()` determines validity. Use `is not None` so a valid zero is retained.
`capture(event)` immediately returns an immutable integer snapshot.
The nested functions access the `dut` passed to the factory. `PythonPredicateTrigger` checks each
`RisingEdge`; `mode="each_sample"` captures consecutive valid cycles without losing the second response.
Capture is synchronous. For structured interfaces, use `Bundle.sample()` rather than handing out a mutable DUT object.

## Read late and verify the stored sample

<!-- executable-example: examples/getting_started/test_accumulator.py#monitor -->
```python
from toffee import Scoreboard


@pytest.mark.asyncio
async def test_delayed_response_snapshot():
    dut = TutorialDut(latency=2)
    driver = make_sync_driver(dut)
    monitor = make_response_monitor(dut)
    checker = Scoreboard("accumulator")
    try:
        async with Execution(dut.backend) as execution:
            async with driver, monitor.start(execution):
                await driver.send(3)
                assert dut.response is None
                await ClockCycles(dut.clock, 3)
                assert dut.response is None

                observed = await monitor.recv()
                checker.check(expected=3, actual=observed.value)
                assert observed.event.tick == 6
    finally:
        dut.close()
```

```bash
python3 -m pytest -q examples/getting_started/test_accumulator.py::test_delayed_response_snapshot
```

Expect `1 passed`. Input is accepted at cycle 1 and the test reads after cycle 4.
Although `dut.response` is then None, `monitor.recv()` retrieves the cycle-3 sample.
`observed.value` is 3 and `observed.event.tick` is 6, the sampling time rather than retrieval time.
`as execution` names the running environment. The combined context starts Driver and Monitor,
then closes them in reverse order. Start the Monitor before sending to observe all responses.

## What does Scoreboard do here?

`checker.check(expected=3, actual=observed.value)` compares values with useful diagnostics.
This still retrieves each response manually; no automatic association or response timeout is configured.
A standalone `recv()` cannot infer a timeout from a missing response.

Change latency from 2 to 1: the value check passes but the time check becomes `assert 4 == 6`.
Only change timing expectations if the specification has changed; restore the example before continuing.
[Assemble the Driver and Monitor into an Agent](agent-test.md).
