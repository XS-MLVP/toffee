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
