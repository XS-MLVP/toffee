# 7. Compute expectations and share environment setup

Replace manual arithmetic for long sequences and reuse environment creation, startup, and cleanup.
This lesson introduces a reference model and a pytest fixture.

## Implement the specification

Continue in `examples/getting_started/test_accumulator.py`:

<!-- executable-example: examples/getting_started/test_accumulator.py#model -->
```python
class AccumulatorModel:
    def __init__(self):
        self.total = 0

    def accept(self, operand):
        self.total = (self.total + operand) % 256
        return self.total
```

`class` defines an object type. `AccumulatorModel()` constructs an instance; `__init__` initializes it.
`self.total` belongs to the model. `accept(operand)` updates that state and returns the expected response.
The model neither reads the DUT nor waits for clocks, and can run independently:

```python
from examples.getting_started.test_accumulator import AccumulatorModel

model = AccumulatorModel()
assert model.accept(250) == 250
assert model.accept(10) == 4
```

Connect it with `agent.connect(model)` before starting the environment.
The Agent advances it **after actual input acceptance**, in acceptance order, not response order.
Queued but unaccepted requests must not advance the model. Tests can then call `send(3)` without an explicit expectation.

## Reuse setup across independent tests

<!-- executable-example: examples/getting_started/test_accumulator.py#fixture -->
```python
import pytest_asyncio


@pytest_asyncio.fixture
async def accumulator():
    dut = TutorialDut()
    agent = make_agent(dut)
    agent.connect(AccumulatorModel())
    try:
        async with Execution(dut.backend, agents=[agent]):
            yield agent
    finally:
        dut.close()


@pytest.mark.asyncio
async def test_model_checks_a_sequence(accumulator):
    for operand in [3, 5, 7]:
        await accumulator.send(operand)
    await accumulator.finish(timeout_cycles=10, observe_cycles=1)


@pytest.mark.asyncio
async def test_model_checks_wraparound(accumulator):
    for operand in [250, 10]:
        await accumulator.send(operand)
    await accumulator.finish(timeout_cycles=10, observe_cycles=1)
```

```bash
python3 -m pytest -v examples/getting_started/test_accumulator.py -k test_model_checks
```

`-k test_model_checks` selects two tests; expect `2 passed` and the other tests deselected.
The first checks 3, 8, 15; the second checks 250, 4. A fresh fixture per test prevents the second
from inheriting the first test's total.

## Understand the fixture lifecycle

`@pytest_asyncio.fixture` registers `accumulator()` as setup code. A test argument named `accumulator`
receives the Agent supplied by `yield agent`.

```text
创建 DUT、模型和 Agent
    ↓
进入 Execution
    ↓
yield agent → 执行一条测试 → 测试结束或抛出异常
    ↓
退出 Execution，再 dut.close()
```

`yield` hands control to the test; execution resumes afterwards for cleanup.
Keep `finish` in the test because budgets and observation windows depend on the scenario.
Closed instances cannot be restarted; create a new environment for each test.

## Introduce a model bug

Temporarily remove `% 256`. The ordinary sequence still passes; the overflow case reports expected 260
and actual 4. Here the model is wrong, not the DUT. Restore the remainder operation.
Validate models with short hand-calculated examples. The teaching DUT sums input history while the model
updates state incrementally; they do not call one another.
Continue with [pipelined submission](pipeline.md).
