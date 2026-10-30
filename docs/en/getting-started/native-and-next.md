# Optional: native clocks and project DUTs

Complete the main tutorial and prepare xcomm's Python bindings. This lesson checks that the same
verification components work with native XClock.

## Verify the loaded binding

Toffee depends on `uc-xcomm`, which provides `xspcomm`. The backend requires APIs including
`XClock.StepHalf`, `GetHalfTick`, `GetPhase`, `XData`, and `XTriggerEngine`.
Use the commit, package, ABI, and protocol versions from the
[dependency manifest](https://github.com/XS-MLVP/toffee/blob/main/.github/native-dependencies.json), not the package name alone.

```bash
python3 examples/getting_started/check_native.py
```

For a binding built locally, add the **parent of the xspcomm package directory** to the search path.
Replace the placeholder with your build directory:

```bash
export PYTHONPATH="/实际路径/xcomm/python${PYTHONPATH:+:$PYTHONPATH}"
python3 examples/getting_started/check_native.py
```

The script prints the loaded binding's path and should end with:

```text
native clock: tick=2, phase=RISING_STABLE
```

| Symptom | Action |
| --- | --- |
| `No module named xspcomm` | Check installation in the selected interpreter or PYTHONPATH |
| Path points at an old build | Select the intended binding |
| Missing half-step APIs or XTriggerEngine | Update or rebuild xcomm with those APIs |

Repository tests attempt to discover a sibling Picker build. Ordinary scripts and example-only runs
do not use `tests/conftest.py`, so xspcomm must be directly importable here.

## Check overflow on a native clock

File: `examples/getting_started/native_lab.py`.

<!-- executable-example: examples/getting_started/native_lab.py -->
```python
"""Optional native-clock exercise, run explicitly after installing xspcomm."""

import pytest

from examples.getting_started.test_accumulator import AccumulatorModel, make_pipeline_agent
from examples.getting_started.toy_dut import TutorialDut
from toffee import Execution


@pytest.mark.asyncio
async def test_accumulator_with_native_clock():
    dut = TutorialDut(backend="native")
    agent = make_pipeline_agent(dut)
    agent.connect(AccumulatorModel())
    try:
        async with Execution(dut.backend, agents=[agent]):
            first = agent.submit(250)
            second = agent.submit(10)
            await agent.finish(timeout_cycles=10, observe_cycles=1)
            assert await first == 250
            assert await second == 4
    finally:
        dut.close()
```

```bash
python3 -m pytest -q examples/getting_started/native_lab.py
```

Expect `1 passed`. The environment changes to `TutorialDut(backend="native")`, using native XClock and XData.
The accumulator behavior is still implemented in Python: this verifies backend integration, not an RTL accumulator.
This optional file is run explicitly; the basic tutorial does not require native bindings to execute its teaching model.

## Replace the teaching DUT with real RTL

DUT generation, ports, and initialization are project-specific. Implement these mappings in the project environment:

| Teaching component | Project responsibility |
| --- | --- |
| `TutorialDut()` | Build/create the generated DUT and initialize it |
| `dut.clock` / `dut.backend` | Actual XClock and `XCommClockBackend(clock)` |
| `Bundle(enable=..., operand=...)` | Ports, widths, and idle values |
| SingleCycleDriver | Real acceptance condition and input timing, or a custom Driver |
| `dut.response is not None` | Response validity and sampling point |
| Integer snapshot | Immutable project response record |
| `AccumulatorModel.accept()` | Functional specification, initial state, and updates |
| Three-cycle response budget | Allowed timing from the specification |
| `dut.close()` | Actual resource cleanup |

Keep the input/wait/check structure, but define protocol, reset, and flush rules explicitly.
Do not copy the tutorial's one-cycle enable rule to unrelated interfaces.

## Choose your next task

| Task | Example or guide |
| --- | --- |
| Multicycle inputs and overlapping stages | [Pipeline examples](../guides/pipeline-examples.md) |
| Passive observation | [Monitors](../guides/monitors.md) and [Agents](../guides/agents-and-reference-models.md) |
| Conditions and timeouts | [Triggers](../guides/triggers.md) |
| Fixed-latency output association | [Scoreboards](../guides/scoreboard.md) |
| Seeds, coverage, and pytest reports | [Verification flow](../guides/verification-flow.md) |
| Existing generated cache DUT | [Cache environment](https://github.com/XS-MLVP/toffee/blob/main/examples/integration/cache/README.md), with extra dependencies |
| Full e203 RTL and coverage flow | [e203 environment](https://github.com/XS-MLVP/toffee/blob/main/examples/integration/e203/README.md) |

For the repository's complete checks:

```bash
python3 -m pytest -q --require-xspcomm
python3 -m pip install -e '.[docs]'
mkdocs build --strict
```

`--require-xspcomm` fails when native bindings are unavailable, preventing skipped native tests from
being mistaken for passing coverage. It is a repository test option, not required by the earlier tutorial commands.
Return to the [tutorial](index.md) or [feature map](../reference/feature-map.md).
