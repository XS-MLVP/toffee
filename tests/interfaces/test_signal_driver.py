import unittest

from toffee import ClockCycles, Execution, MemoryBackend, SignalDriver


class Signal:
    def __init__(self, *, output=False):
        self.value = 0
        self.output = output

    def Set(self, value):
        self.value = value

    def IsOutIO(self):
        return self.output


class ScalarDriver(SignalDriver[int]):
    def __init__(self, clock, signal):
        super().__init__((signal,), name="scalar")
        self.clock = clock
        self.signal = signal

    async def send(self, transaction):
        self._claim()
        self.signal.Set(transaction)
        return await ClockCycles(self.clock, 1)


class EmptyDriver(SignalDriver[int]):
    async def send(self, transaction):
        raise NotImplementedError


class SignalDriverTests(unittest.IsolatedAsyncioTestCase):
    async def test_scalar_driver_needs_no_bundle_or_interface(self):
        clock = object()
        signal = Signal()
        async with Execution(MemoryBackend(clock)):
            async with ScalarDriver(clock, signal) as driver:
                event = await driver.send(7)
                self.assertEqual(signal.value, 7)
                self.assertEqual(event.tick, 2)

    async def test_signal_ownership_and_direction_are_shared(self):
        clock = object()
        signal = Signal()
        async with Execution(MemoryBackend(clock)):
            first = ScalarDriver(clock, signal)
            second = ScalarDriver(clock, signal)
            async with first:
                with self.assertRaisesRegex(RuntimeError, "already have a driver"):
                    await second.__aenter__()
            async with second:
                await second.send(1)

        async with Execution(MemoryBackend(clock)):
            with self.assertRaisesRegex(ValueError, "cannot drive DUT outputs"):
                async with ScalarDriver(clock, Signal(output=True)):
                    pass

    def test_empty_signal_set_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "at least one driven signal"):
            EmptyDriver((), name="empty")
