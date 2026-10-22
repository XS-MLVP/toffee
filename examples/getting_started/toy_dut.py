"""Teaching substitute, not RTL: an 8-bit accumulator with a delayed response.

At each rising edge, enable=1 accepts operand once. Total wraps at 256.
Response is an integer for one cycle, or None when there is no response.
The DUT sums input history, independently of the incremental reference model.
Faults are only for the debugging exercises.
"""

from toffee import MemoryBackend, XCommClockBackend, XPhase


class Signal:
    def __init__(self, width):
        self.width = width
        self.value = 0

    def Set(self, value):
        self.value = int(value) & ((1 << self.width) - 1)

    def U(self):
        return self.value

    def W(self):
        return self.width


class TutorialDut:
    def __init__(self, *, latency=2, fault=None, backend="memory"):
        if latency < 0:
            raise ValueError("latency must be nonnegative")
        if fault not in (None, "wrong", "missing", "late", "extra"):
            raise ValueError("unknown teaching fault")
        if backend not in ("memory", "native"):
            raise ValueError("unknown backend")
        self.latency = latency
        self.fault = fault
        self.cycle = 0
        self.response = None
        self.operands = []
        self.scheduled = {}
        self.accepted_cycles = []
        self.response_cycles = []
        self.native = backend == "native"
        if self.native:
            import xspcomm

            self.clock = xspcomm.XClock(lambda _: 0)
            self.enable = xspcomm.XData(1, xspcomm.XData.InOut)
            self.operand = xspcomm.XData(8, xspcomm.XData.InOut)
            self.total = xspcomm.XData(8, xspcomm.XData.InOut)
            self.backend = XCommClockBackend(self.clock)
            self.clock.StepRis(self._native_sample)
        else:
            self.clock = object()
            self.enable = Signal(1)
            self.operand = Signal(8)
            self.total = Signal(8)
            self.backend = MemoryBackend(self.clock, on_phase=self._sample)

    def _native_sample(self, cycle):
        self._sample(XPhase.RISING_STABLE, cycle * 2)

    def _sample(self, phase, tick):
        if phase is not XPhase.RISING_STABLE:
            return
        self.cycle += 1
        self.response = None
        if self.enable.U():
            self.operands.append(self.operand.U())
            self.accepted_cycles.append(self.cycle)
            self.total.Set(sum(self.operands))
            delay = 5 if self.fault == "late" else self.latency
            due = self.cycle + delay
            result = self.total.U()
            if self.fault == "wrong":
                result = (result + 1) % 256
            if self.fault != "missing":
                self.scheduled[due] = result
            if self.fault == "extra":
                self.scheduled[due + 1] = result
        if self.cycle in self.scheduled:
            self.response = self.scheduled.pop(self.cycle)
            self.response_cycles.append(self.cycle)

    def close(self):
        try:
            self.backend.close()
        finally:
            if self.native:
                self.clock.RemoveStepRisCbByDesc("_native_sample")
