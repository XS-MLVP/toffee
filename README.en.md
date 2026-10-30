# Toffee

[中文](https://github.com/XS-MLVP/toffee/blob/main/README.md) · English

[![PyPI](https://img.shields.io/pypi/v/uc-toffee?style=flat-square&logo=pypi&logoColor=white)](https://pypi.org/project/uc-toffee/)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue?style=flat-square&logo=python&logoColor=white)](https://github.com/XS-MLVP/toffee/blob/main/docs/en/getting-started/installation.md)
[![CI](https://img.shields.io/github/actions/workflow/status/XS-MLVP/toffee/ci.yml?branch=main&style=flat-square&label=CI&logo=github)](https://github.com/XS-MLVP/toffee/actions/workflows/ci.yml?query=branch%3Amain)
[![License](https://img.shields.io/badge/license-MulanPSL--2.0-blue?style=flat-square)](https://github.com/XS-MLVP/toffee/blob/main/LICENSE)
[![Docs](https://img.shields.io/badge/docs-Guide-0A7EA4?style=flat-square)](https://github.com/XS-MLVP/toffee/blob/main/docs/en/index.md)
[![GitHub Stars](https://img.shields.io/github/stars/XS-MLVP/toffee?style=social)](https://github.com/XS-MLVP/toffee/tree/main)

Toffee is a Python hardware verification framework. Describe input timing and condition waits with
`async` / `await`, run tests with pytest, and build verification environments with reference models,
checking, and coverage.

- **Clocks and events:** wait for clock edges, signal conditions, and multi-cycle events, with
  supported conditions compiled for native evaluation.
- **Driving and checking:** organize reusable Drivers, Monitors, and Agents; check ordered or
  reordered responses with Scoreboard.
- **Coverage and regressions:** define functional, cross, and temporal coverage, then aggregate
  functional and native coverage after parallel regressions.

## Quick start

Requires **Python 3.11+**. The PyPI distribution is `uc-toffee`, imported as `toffee`; dependency
`uc-xcomm` supplies native bindings. Install with `python3 -m pip install "uc-toffee[runner]"`.
To run the repository examples, create an environment and install from source at the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e '.[test]'
```

This test uses the included 8-bit accumulator model and needs no RTL simulator. It supplies 3,
waits one clock cycle, and checks the accumulated result and idle behavior:

```python
import pytest

from toffee import ClockCycles, Execution
from examples.getting_started.toy_dut import TutorialDut


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

The test is already in the [example file](https://github.com/XS-MLVP/toffee/blob/main/examples/getting_started/test_accumulator.py).
Run it directly:

```bash
python3 -m pytest -q examples/getting_started/test_accumulator.py::test_one_input_and_idle
```

Expect `1 passed`. Continue with the [accumulator tutorial](https://github.com/XS-MLVP/toffee/blob/main/docs/en/getting-started/index.md)
to add boundary tests, Drivers, Monitors, reference models, and coverage.

## Use Toffee in a project

For real RTL, use a Picker-generated DUT Python package and connect its xspcomm clock/signals
through `XCommClockBackend`. See [real DUT integration](https://github.com/XS-MLVP/toffee/blob/main/docs/en/getting-started/native-and-next.md)
for preparation and dependencies.

Use `toffee run` to execute larger regressions in parallel and aggregate coverage. For example:

```bash
toffee run examples/getting_started --workers 4
```

The terminal prints the run's artifact directory, including `coverage.html` when coverage data
exists. Replace the example path with your project's test directory. See the
[regression guide](https://github.com/XS-MLVP/toffee/blob/main/docs/en/guides/runner.md) for options.

## Documentation

- [Tutorials](https://github.com/XS-MLVP/toffee/blob/main/docs/en/getting-started/index.md): from one assertion to a complete environment.
- [How-to guides](https://github.com/XS-MLVP/toffee/blob/main/docs/en/guides/index.md): connect DUTs, organize components, collect coverage, and run regressions.
- [Reference](https://github.com/XS-MLVP/toffee/blob/main/docs/en/reference/index.md): APIs, CLI options, and behavioral limits.
- [Explanation](https://github.com/XS-MLVP/toffee/blob/main/docs/en/explanation/index.md): scheduling, ownership, and coverage interpretation.
- [Runnable examples](https://github.com/XS-MLVP/toffee/blob/main/docs/en/examples.md) · [Contributing](https://github.com/XS-MLVP/toffee/blob/main/CONTRIBUTING.md)

## Toffee Legacy

Legacy Toffee remains on the [`master` branch](https://github.com/XS-MLVP/toffee/tree/master).
The current version is a **breaking change** and is incompatible with the Toffee Legacy API.
