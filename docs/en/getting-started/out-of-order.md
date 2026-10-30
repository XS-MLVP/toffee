# Optional: match responses that return out of order

Complete [pipelined submission](pipeline.md) first. FIFO matching works only when responses follow
acceptance order. An interface that reorders responses must expose an association key, such as a tag;
the framework cannot infer association from response values.

## Use the tagged accumulator variant

`examples/transactions/reference_model.py` provides a separate variant without changing `TutorialDut`.
It accepts positive operands with tags 1, 2, and 3; tag 0 means idle. This example does not overflow.
Results are computed in **acceptance order**, with deliberately different response latencies:

| tag | operand | Expected total | Acceptance cycle | Latency | Response cycle |
| --- | --- | --- | --- | --- | --- |
| 1 | 3 | 3 | 1 | 4 | 5 |
| 2 | 5 | 8 | 2 | 2 | 4 |
| 3 | 7 | 15 | 3 | 3 | 6 |

Response tags arrive as 2, 1, 3, while expected cumulative totals remain 3, 8, 15.
Do not advance the model in completion order.
`Command(tag=1, operand=3)` and `Result(tag=1, total=3)` are immutable dataclass records;
they contain data and do not advance simulation.

## Configure association

The variant's Agent sets `request_key=lambda request: request.tag` and
`response_key=lambda response: response.tag`. These functions extract matching keys.
Two outstanding requests must not share the same key.
The Driver writes tag and operand and restores idle after acceptance; the Monitor captures tagged responses.
See the [complete source](https://github.com/XS-MLVP/toffee/blob/main/examples/transactions/reference_model.py).

## Run the example

File: `examples/getting_started/test_tutorial_out_of_order.py`.

<!-- executable-example: examples/getting_started/test_tutorial_out_of_order.py -->
```python
import pytest

from examples.transactions.reference_model import (
    AccumulatorAgent, AccumulatorDut, AccumulatorModel, Command, Result,
)
from toffee import Execution


@pytest.mark.asyncio
async def test_tagged_responses_arrive_out_of_order():
    dut = AccumulatorDut("memory")
    agent = AccumulatorAgent(dut, response_timeout_cycles=5)
    agent.connect(AccumulatorModel())
    try:
        async with Execution(dut.backend, agents=[agent]):
            first = agent.submit(Command(tag=1, operand=3))
            second = agent.submit(Command(tag=2, operand=5))
            third = agent.submit(Command(tag=3, operand=7))
            await agent.finish(timeout_cycles=20, observe_cycles=1)

            assert dut.emitted_tags == [2, 1, 3]
            assert await first == Result(tag=1, total=3)
            assert await second == Result(tag=2, total=8)
            assert await third == Result(tag=3, total=15)
    finally:
        dut.close()
```

```bash
python3 -m pytest -q examples/getting_started/test_tutorial_out_of_order.py
```

Expect `1 passed`. `first` retrieves tag 1, not whichever response arrived first.
Awaiting handles in submission order does not change the physical response order.
Change the expected emitted tags to `[1, 2, 3]` to confirm that the ordering assertion fails.
Restore it, then try an incorrect first result of `Result(tag=1, total=8)` to trigger a value mismatch.

Use keys only when the interface carries observable association information. If untagged responses can
reorder, first establish a valid association rule; a Python-only key cannot invent missing hardware information.
See [native integration](native-and-next.md) or return to the [tutorial overview](index.md).
