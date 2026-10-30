# 3. Test multiple data sets and boundaries

A passing “add 3 and hold for two cycles” test covers one scenario. Now check accumulation and boundaries.

## Write the cases first

| Input sequence | Expected final total | Scenario |
| --- | --- | --- |
| 0 | 0 | Zero is valid input, not absence of input |
| 3, 5 | 8 | State accumulates across requests |
| 255 | 255 | Maximum input |
| 255, 1 | 0 | Overflow exactly to zero |
| 250, 10 | 4 | Keep the low eight bits after overflow |

Derive expectations from the specification, not `dut.total.U()`, which would compare the DUT with itself.

## Run each row with pytest

Continue in `examples/getting_started/test_accumulator.py`:

<!-- executable-example: examples/getting_started/test_accumulator.py#cases -->
```python
@pytest.mark.asyncio
@pytest.mark.parametrize("operands, expected", [
    ([0], 0),
    ([3, 5], 8),
    ([255], 255),
    ([255, 1], 0),
    ([250, 10], 4),
])
async def test_accumulator_cases(operands, expected):
    dut = TutorialDut()
    try:
        async with Execution(dut.backend):
            for operand in operands:
                dut.operand.Set(operand)
                dut.enable.Set(1)
                await ClockCycles(dut.clock, 1)
                dut.enable.Set(0)
            assert dut.total.U() == expected
    finally:
        dut.close()
```

```bash
python3 -m pytest -v examples/getting_started/test_accumulator.py::test_accumulator_cases
```

Expect five `PASSED` entries and `5 passed`. `-v` lists each case.
`[3, 5]` is a list; `for operand in operands:` visits its values in order.
Each iteration writes input, waits for an edge, and clears enable.
`@pytest.mark.parametrize("operands, expected", [...])` calls the test once per row.
Each invocation creates a fresh DUT, so cases do not inherit prior state.

## Add a boundary case

Add `([128, 128], 0)` and expect `6 passed`. Writing 256 as the expectation makes only this new case fail,
because an 8-bit register cannot hold 256.
These tests check final state, not yet every response or whether all responses arrive.
The repeated write/enable/wait/disable sequence is the next abstraction to extract:
[define a Driver](driver.md).
