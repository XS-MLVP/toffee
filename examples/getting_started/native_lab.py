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
