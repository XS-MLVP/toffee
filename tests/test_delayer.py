import asyncio

import toffee_test

import toffee
from toffee.triggers import *


class FakeXData:
    def __init__(self):
        self.value = 0


class DUT:
    def __init__(self):
        self.event = asyncio.Event()
        self.a = FakeXData()
        self.b = FakeXData()

    def Step(self, cycles): ...


@toffee_test.testcase
async def test_delayer():
    dut = DUT()
    dut.event.clear()
    toffee.start_clock(dut)

    delayed_a = toffee.Delayer(dut.a, 2)
    delayed_b = toffee.Delayer(dut.b, 3)

    for i in range(10):
        dut.a.value = i
        dut.b.value = i + 1
        await ClockCycles(dut)

        if i < 2:
            assert delayed_a.value is None
        else:
            assert delayed_a.value == dut.a.value - 2

        if i < 3:
            assert delayed_b.value is None
        else:
            assert delayed_b.value == dut.b.value - 3
