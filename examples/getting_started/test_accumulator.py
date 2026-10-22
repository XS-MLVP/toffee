"""Read from top to bottom alongside docs/getting-started.

Each region is introduced by one lesson. Imports appear when first needed.
Run one test with: python3 -m pytest -q <this-file>::<test-name>
"""

# region assertion
def test_addition():
    expected = 8
    actual = 3 + 5
    assert actual == expected
# endregion

# region clock
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
# endregion

# region cases
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
# endregion

# region driver_setup
from toffee import Bundle, SyncSingleCycleDriver


def encode_operand(operand):
    return {"enable": 1, "operand": operand}


def make_sync_driver(dut):
    return SyncSingleCycleDriver(
        dut.clock,
        Bundle(enable=dut.enable, operand=dut.operand),
        idle={"enable": 0, "operand": 0},
        encoder=encode_operand,
    )
# endregion

# region driver
@pytest.mark.asyncio
async def test_driver_restores_idle():
    dut = TutorialDut()
    driver = make_sync_driver(dut)
    try:
        async with Execution(dut.backend):
            async with driver:
                await driver.send(3)
                await driver.send(5)
                assert dut.total.U() == 8
                assert dut.enable.U() == 0

                await ClockCycles(dut.clock, 2)
                assert dut.total.U() == 8
    finally:
        dut.close()
# endregion

# region monitor_setup
from toffee import PythonPredicateTrigger, RisingEdge, SamplingMonitor


def make_response_monitor(dut):
    def response_present():
        return dut.response is not None

    def capture(event):
        return dut.response

    return SamplingMonitor(
        PythonPredicateTrigger(
            "response-present", response_present,
            sample=RisingEdge(dut.clock), mode="each_sample",
        ),
        capture=capture,
    )
# endregion

# region monitor
from toffee import Scoreboard


@pytest.mark.asyncio
async def test_delayed_response_snapshot():
    dut = TutorialDut(latency=2)
    driver = make_sync_driver(dut)
    monitor = make_response_monitor(dut)
    checker = Scoreboard("accumulator")
    try:
        async with Execution(dut.backend) as execution:
            async with driver, monitor.start(execution):
                await driver.send(3)
                assert dut.response is None
                await ClockCycles(dut.clock, 3)
                assert dut.response is None

                observed = await monitor.recv()
                checker.check(expected=3, actual=observed.value)
                assert observed.event.tick == 6
    finally:
        dut.close()
# endregion

# region agent_setup
from toffee import Agent


def make_agent(dut):
    return Agent(
        "accumulator",
        driver=make_sync_driver(dut),
        monitors={"response": make_response_monitor(dut)},
        response_monitor="response",
        clock=dut.clock,
        response_timeout_cycles=3,
    )
# endregion

# region agent
@pytest.mark.asyncio
async def test_agent_checks_responses():
    dut = TutorialDut()
    agent = make_agent(dut)
    try:
        async with Execution(dut.backend, agents=[agent]):
            await agent.send(3, expected=3)
            await agent.send(5, expected=8)
            await agent.finish(timeout_cycles=10, observe_cycles=1)
    finally:
        dut.close()
# endregion

# region model
class AccumulatorModel:
    def __init__(self):
        self.total = 0

    def accept(self, operand):
        self.total = (self.total + operand) % 256
        return self.total
# endregion

# region fixture
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
# endregion

# region pipeline_setup
from toffee import AsyncSingleCycleDriver


def make_pipeline_agent(dut, *, response_timeout_cycles=3):
    driver = AsyncSingleCycleDriver(
        dut.clock,
        Bundle(enable=dut.enable, operand=dut.operand),
        idle={"enable": 0, "operand": 0},
        encoder=encode_operand,
    )
    return Agent(
        "accumulator",
        driver=driver,
        monitors={"response": make_response_monitor(dut)},
        response_monitor="response",
        clock=dut.clock,
        response_timeout_cycles=response_timeout_cycles,
    )
# endregion

# region pipeline
@pytest.mark.asyncio
async def test_three_inputs_in_flight():
    dut = TutorialDut(latency=2)
    agent = make_pipeline_agent(dut)
    agent.connect(AccumulatorModel())
    try:
        async with Execution(dut.backend, agents=[agent]):
            first = agent.submit(3)
            second = agent.submit(5)
            third = agent.submit(7)

            await agent.finish(timeout_cycles=10, observe_cycles=1)
            assert await first == 3
            assert await second == 8
            assert await third == 15
            assert dut.accepted_cycles == [1, 2, 3]
            assert dut.response_cycles == [3, 4, 5]
    finally:
        dut.close()
# endregion

# region batches
@pytest.mark.asyncio
async def test_two_batches_and_withdrawn_input():
    dut = TutorialDut()
    agent = make_pipeline_agent(dut)
    agent.connect(AccumulatorModel())
    try:
        async with Execution(dut.backend, agents=[agent]):
            first = agent.submit(3)
            withdrawn = agent.submit(99)
            withdrawn.cancel()
            await agent.drain(timeout_cycles=10)
            assert await first == 3

            second = agent.submit(5)
            await agent.finish(timeout_cycles=10, observe_cycles=1)
            assert await second == 8
            assert dut.operands == [3, 5]
    finally:
        dut.close()
# endregion

# region coverage
from toffee import Bin, CoverGroupDef, CoverPointDef, CoverageDatabase


@pytest.mark.asyncio
async def test_result_coverage(tmp_path):
    definition = CoverGroupDef("accumulator", (
        CoverPointDef("result", {
            "zero": Bin.values(0),
            "middle": Bin.range(1, 254),
            "maximum": Bin.values(255),
        }),
    ))
    coverage = definition.instantiate("tutorial")
    dut = TutorialDut()
    agent = make_pipeline_agent(dut)
    agent.connect(AccumulatorModel())
    try:
        async with Execution(dut.backend, agents=[agent]):
            for operand in [0, 255, 2]:
                transfer = agent.submit(operand)
                result = await transfer
                coverage.sample({"result": result})
            await agent.finish(timeout_cycles=10, observe_cycles=1)
        coverage.assert_coverage(100)
        print(f"Result bins: {coverage.coverage:.0f}%")
    finally:
        report = tmp_path / "functional.json"
        try:
            CoverageDatabase([coverage]).write_json(report)
            print(f"Coverage JSON: {report}")
        finally:
            dut.close()
# endregion
