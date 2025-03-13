import asyncio

import toffee
from toffee import *

"""Fake classes for testing"""

class FXData: ...

class FXPin:
    def __init__(self):
        self.xdata, self.event, self.value, self.mIOType = FXData(), None, 0, 0

class FDUT:
    def __init__(self):
        self.event = asyncio.Event()

    def set_FXPin_event(self):
        for attr in dir(self):
            if isinstance(getattr(self, attr), FXPin):
                getattr(self, attr).event = self.event

    def StepRis(self, func): ...

    def Step(self, cycles): ...

"""Adder class for testing"""

class AdderBundle(Bundle):
    a, b, sum = Signals(3)

class Adder(FDUT):
    def __init__(self):
        super().__init__()
        self.io_a, self.io_b, self.io_sum = FXPin(), FXPin(), FXPin()
        self.set_FXPin_event()

    def Step(self, cycles):
        self.io_sum.value = self.io_a.value + self.io_b.value
