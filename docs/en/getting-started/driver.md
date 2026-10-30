# 4. Move repeated input timing into a Driver

Let tests say “add 3” while defining signal writes, hold times, and idle values in one place.
A Driver encapsulates the project's input protocol.

## Choose a Driver that matches the timing

This DUT accepts at the next rising edge, so `SyncSingleCycleDriver` fits.
Continue in `examples/getting_started/test_accumulator.py`:

<!-- executable-example: examples/getting_started/test_accumulator.py#driver_setup -->
```python
from toffee import Bundle, SyncSingleCycleDriver


def encode_operand(operand):
    return {"enable": 1, "operand": operand}


def make_sync_driver(dut):
    return SyncSingleCycleDriver(
        dut.clock,
        Bundle(enable=dut.enable, operand=dut.operand),
        idle={"enable": 0, "operand": 0},
        encoder=encode_operand,
    )
```

`Bundle` groups `enable` and `operand`. A dictionary maps each field to its value.
`encode_operand(3)` returns `{"enable": 1, "operand": 3}`. Passing `encoder=encode_operand` without
parentheses gives the Driver a function to call for each request. `idle` supplies the values restored afterwards.
`make_sync_driver(dut)` is an ordinary project factory, not a special framework type.

## Repeat the same test

<!-- executable-example: examples/getting_started/test_accumulator.py#driver -->
```python
@pytest.mark.asyncio
async def test_driver_restores_idle():
    dut = TutorialDut()
    driver = make_sync_driver(dut)
    try:
        async with Execution(dut.backend):
            async with driver:
                await driver.send(3)
                await driver.send(5)
                assert dut.total.U() == 8
                assert dut.enable.U() == 0

                await ClockCycles(dut.clock, 2)
                assert dut.total.U() == 8
    finally:
        dut.close()
```

```bash
python3 -m pytest -q examples/getting_started/test_accumulator.py::test_driver_restores_idle
```

Expect `1 passed`. Two sends accept 3 and 5; after two idle cycles the total remains 8.
`async with driver` manages this standalone Driver and releases its input resources.
Lesson 6 places it inside an Agent managed by Execution.

## What does send wait for?

`await driver.send(3)` waits for **input acceptance** and returns its event, not the response two cycles later.
Sync means the calling coroutine waits for the input operation; it does not block the entire Python process.
Calling `driver.send(3)` without awaiting it does not complete the send.

Change the encoder's enable value to 0: the test reads 0 instead of 8 because no valid input was driven.
Restore it to 1. Use SingleCycle only when the interface really accepts in one cycle.
For multicycle or staged protocols, implement the corresponding [SyncDriver or AsyncDriver](../guides/drivers.md)
and return the actual acceptance event.

Continue with [capturing delayed outputs](monitor.md).
