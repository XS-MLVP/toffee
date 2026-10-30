# 2. Drive inputs, wait for a clock, and check the result

Replace the arithmetic expression with a clocked DUT. Check that adding 3 works and that the value stays constant while idle.

## State the DUT specification

The tutorial accumulator starts at 0 and examines its inputs on every rising edge:

- With `enable=1`, accept `operand` and update `total = (total + operand) % 256`.
- With `enable=0`, accept nothing and retain `total`.
- Each accepted input also produces a response **two cycles later**. This lesson reads `total`; lesson 5 checks responses.

There is no backpressure or ready/valid handshake here. These are this DUT's rules, not assumptions
made by Toffee. Holding `enable=1` across two rising edges accepts two inputs.
`TutorialDut()` from `examples/getting_started/toy_dut.py` is a Python teaching model, requiring no RTL build.
Each instance starts fresh; real designs need initialization specified by the project.

| Interface | Meaning |
| --- | --- |
| `dut.operand.Set(3)` | Write an operand in the range 0–255 |
| `dut.enable.Set(1)` / `.Set(0)` | Enable or disable acceptance at the next rising edge |
| `dut.total.U()` | Read the current unsigned total |
| `dut.clock` | Clock used by waits |
| `dut.backend` | Backend supplied to Execution |
| `dut.close()` | Release the teaching environment |

## Run the test

This excerpt extends `examples/getting_started/test_accumulator.py`; retain earlier definitions:

<!-- executable-example: examples/getting_started/test_accumulator.py#clock -->
```python
import pytest

from examples.getting_started.toy_dut import TutorialDut
from toffee import ClockCycles, Execution


@pytest.mark.asyncio
async def test_one_input_and_idle():
    dut = TutorialDut()
    try:
        async with Execution(dut.backend):
            assert dut.total.U() == 0
            dut.operand.Set(3)
            dut.enable.Set(1)
            await ClockCycles(dut.clock, 1)
            dut.enable.Set(0)
            assert dut.total.U() == 3

            await ClockCycles(dut.clock, 2)
            assert dut.total.U() == 3
    finally:
        dut.close()
```

```bash
python3 -m pytest -q examples/getting_started/test_accumulator.py::test_one_input_and_idle
```

Expect `1 passed`.

## Read the Python constructs

`import` obtains names from modules. `@pytest.mark.asyncio` marks an asynchronous test;
`async def` permits `await`. Here `await` waits for a simulation condition, not a second of wall time.
`async with Execution(dut.backend):` starts the environment and closes it when the block exits.
`await ClockCycles(dut.clock, 1)` waits until sampling at the next rising edge has completed.
Ordinary Python assignments alone do not advance the clock.
`try ... finally` ensures that `dut.close()` runs even when an assertion fails.

## Follow the cycles

| Time | enable | Action | total |
| --- | --- | --- | --- |
| DUT creation | 0 | Initial state | 0 |
| Before first edge | 1 | Write operand 3 | 0 |
| First rising edge | 1 | Accept 3 | 3 |
| After first edge | 0 | Deassert enable | 3 |
| Second and third edges | 0 | No new input | 3 |

Comment out `dut.enable.Set(0)` and rerun. The final assertion becomes `assert 9 == 3` because
3 was accepted three times. Restore the line. A simulation cycle is not a Python loop iteration.
This backend uses two half-ticks per cycle; a new clock's first rising edge is tick 2.

Continue with [multiple data sets and boundaries](more-cases.md).
