import random

import toffee_test
from env import AdderBundle
from env import AdderEnv

import toffee

"""
Test cases
"""


@toffee_test.testcase
async def test_random(adder_env):
    for _ in range(1000):
        a = random.randint(0, 2**64 - 1)
        b = random.randint(0, 2**64 - 1)
        cin = random.randint(0, 1)
        await adder_env.add_agent.exec_add(a, b, cin)


@toffee_test.testcase
async def test_boundary(adder_env):
    for cin in [0, 1]:
        for a in [0, 2**64 - 1]:
            for b in [0, 2**64 - 1]:
                await adder_env.add_agent.exec_add(a, b, cin)


"""
Coverage definition
"""

import toffee.funcov as fc
from toffee.funcov import CovGroup


def adder_cover_point(adder):
    g = CovGroup("Adder addition function")

    g.add_cover_point(adder.io_cout, {"io_cout is 0": fc.Eq(0)}, name="Cout is 0")
    g.add_cover_point(adder.io_cout, {"io_cout is 1": fc.Eq(1)}, name="Cout is 1")
    g.add_cover_point(adder.io_cin, {"io_cin is 0": fc.Eq(0)}, name="Cin is 0")
    g.add_cover_point(adder.io_cin, {"io_cin is 1": fc.Eq(1)}, name="Cin is 1")
    g.add_cover_point(adder.io_a, {"a > 0": fc.Gt(0)}, name="signal a set")
    g.add_cover_point(adder.io_b, {"b > 0": fc.Gt(0)}, name="signal b set")
    g.add_cover_point(adder.io_sum, {"sum > 0": fc.Gt(0)}, name="signal sum set")
    g.add_cover_point(
        (toffee.Delayer(adder.io_cout, 1), adder.io_cout),
        {
            "io_cout 1 -> 0": lambda signal: signal[0].value == 1
            and signal[1].value == 0
        },
        name="Cout 1 -> 0",
    )

    return g


"""
Initialize before each test
"""

from picker_out_adder import DUTAdder


@toffee_test.fixture
async def adder_env(toffee_request: toffee_test.ToffeeRequest):
    toffee.setup_logging(toffee.WARNING)
    dut = toffee_request.create_dut(DUTAdder)
    toffee_request.add_cov_groups(adder_cover_point(dut))
    toffee.start_clock(dut)
    return AdderEnv(AdderBundle.from_prefix("io_").bind(dut))
