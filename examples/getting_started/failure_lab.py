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
