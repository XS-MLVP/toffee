# 9. Understand failure diagnostics

Run four deliberate faults and learn what the checker reports. These commands are expected to fail.

## Confirm the working example first

```bash
python3 -m pytest -q examples/getting_started/test_accumulator.py::test_three_inputs_in_flight
```

After `1 passed`, use `failure_lab.py`. It keeps the same Driver, Monitor, and model while making the
teaching DUT return a wrong, missing, late, or extra response. Its filename does not start with `test_`,
so normal directory discovery does not run it.

File: `examples/getting_started/failure_lab.py`.

<!-- executable-example: examples/getting_started/failure_lab.py -->
```python
"""Intentionally failing exercises; run one case explicitly with pytest -k.

This filename does not start with test_, so the regular suite skips it.
"""

import pytest

from examples.getting_started.test_accumulator import AccumulatorModel, make_pipeline_agent
from examples.getting_started.toy_dut import TutorialDut
from toffee import Execution


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["wrong", "missing", "late", "extra"])
async def test_find_the_fault(fault):
    dut = TutorialDut(fault=fault)
    agent = make_pipeline_agent(dut)
    agent.connect(AccumulatorModel())
    try:
        async with Execution(dut.backend, agents=[agent]):
            agent.submit(3)
            await agent.finish(timeout_cycles=10, observe_cycles=2)
    finally:
        dut.close()
```

The test does not retain or await the submitted handle. `finish()` must still report background failures.

## Wrong result

```bash
python3 -m pytest -q examples/getting_started/failure_lab.py -k wrong --tb=short
```

`-k wrong` selects that case; `--tb=short` shortens the traceback.
Expect `1 failed, 3 deselected`, with diagnostics like:

```text
ScoreboardMismatch: accumulator mismatch
  request: 3
  accepted_tick: 2
  observed_tick: 6
  expected: 3
  actual:   4
```

Read request, expected, and actual first, then the sampling ticks.
The injected fault adds 1 to the response; a longer timeout cannot fix it.

## Missing and late responses

```bash
python3 -m pytest -q examples/getting_started/failure_lab.py -k missing --tb=short
python3 -m pytest -q examples/getting_started/failure_lab.py -k late --tb=short
```

Each command produces `1 failed, 3 deselected` and `ScoreboardTimeoutError`.
Acceptance is at tick 2; a three-cycle budget sets the deadline to tick 8.
The following half-cycle barrier reports `deadline exceeded at tick 9`; a correct response at tick 8 is accepted.

The missing case never responds; the late case waits five cycles. A timeout alone cannot distinguish them.
Use waveforms or DUT logs to determine whether output eventually appears.
Increase the budget only when the specification permits it.

## An extra response after a correct one

```bash
python3 -m pytest -q examples/getting_started/failure_lab.py -k extra --tb=short
```

Expect `ScoreboardAssociationError` containing `1 unmatched observation(s)`.
The first response is correct; the extra response appears one cycle later and is caught by
`observe_cycles=2`. Waiting only for the last expected response would miss it.

## Decide what to investigate

| Symptom | Check first | Avoid assuming |
| --- | --- | --- |
| AssertionError / ScoreboardMismatch | Specification, expectation, input encoding, sampled snapshot | The DUT must be wrong |
| ScoreboardTimeoutError | Acceptance time, response budget, sampling condition | Any larger timeout is valid |
| ScoreboardAssociationError | Extra/duplicate output, keys, accidental idle sampling | Extra samples can simply be discarded |
| Unfinished work at exit | finish usage and bounded completion | Closing automatically means success |

Run the file without `-k` to see `4 failed`. The normal tutorial regression,
`python3 -m pytest -q examples/getting_started`, should pass.
Continue with [functional coverage](coverage.md).
