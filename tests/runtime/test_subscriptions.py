import asyncio
import unittest

from toffee import (
    ClockCycles,
    FallingEdge,
    MemoryBackend,
    RisingEdge,
    Execution,
    Value,
    on,
)


class SubscriptionTests(unittest.IsolatedAsyncioTestCase):
    async def test_capture_must_be_synchronous(self):
        clock = object()

        async def capture(event):
            return event

        with self.assertRaisesRegex(TypeError, "capture must be synchronous"):
            on(FallingEdge(clock), capture=capture)

    async def test_on_value_defaults_to_enter_not_level_repetition(self):
        class Signal:
            value = 1

        clock = object()
        signal = Signal()
        received = []

        @on(Value(signal, 1, sample=RisingEdge(clock)), capacity=8)
        async def observe(event):
            received.append((event.tick, event.value))

        backend = MemoryBackend(clock)
        async with Execution(backend) as execution:
            subscription = execution.subscribe(observe.bind())
            await ClockCycles(clock, 3)
            self.assertEqual(received, [(2, 1)])

            await FallingEdge(clock)
            signal.value = 0
            await RisingEdge(clock)
            await FallingEdge(clock)
            signal.value = 1
            await RisingEdge(clock)
            execution.reactor.cancel_subscription(subscription)

        self.assertEqual(received, [(2, 1), (10, 1)])

    async def test_on_rearms_before_handler_and_delivers_in_order(self):
        clock = object()
        received = []
        three_events = asyncio.Event()

        @on(FallingEdge(clock), capacity=8)
        async def observe(event):
            received.append(event.tick)
            if len(received) == 3:
                three_events.set()
            await asyncio.sleep(0)

        backend = MemoryBackend(clock)
        async with Execution(backend) as execution:
            subscription = execution.subscribe(observe.bind())
            await three_events.wait()
            execution.reactor.cancel_subscription(subscription)

        self.assertEqual(received[:3], [1, 3, 5])
        self.assertEqual(backend.watcher_count, 0)

    async def test_lossless_overflow_fails_execution_explicitly(self):
        clock = object()
        handler_gate = asyncio.Event()

        @on(FallingEdge(clock), capacity=1)
        async def blocked_handler(event):
            await handler_gate.wait()

        backend = MemoryBackend(clock)
        with self.assertRaisesRegex(RuntimeError, "queue overflow"):
            async with Execution(backend) as execution:
                execution.subscribe(blocked_handler.bind())
                await ClockCycles(clock, 10)

    async def test_latest_policy_is_explicit_and_bounded(self):
        clock = object()
        handler_gate = asyncio.Event()
        received = []

        @on(FallingEdge(clock), delivery="latest", capacity=1)
        async def slow_handler(event):
            received.append(event.tick)
            if len(received) == 1:
                await handler_gate.wait()

        backend = MemoryBackend(clock)
        async with Execution(backend) as execution:
            subscription = execution.subscribe(slow_handler.bind())
            await ClockCycles(clock, 5)
            handler_gate.set()
            await asyncio.sleep(0)
            execution.reactor.cancel_subscription(subscription)

        self.assertEqual(received[0], 1)
        self.assertGreaterEqual(received[-1], 7)

    async def test_handler_exception_fails_execution(self):
        clock = object()

        @on(FallingEdge(clock))
        async def broken_handler(event):
            raise ValueError("scoreboard failed")

        backend = MemoryBackend(clock)
        with self.assertRaisesRegex(ValueError, "scoreboard failed"):
            async with Execution(backend) as execution:
                execution.subscribe(broken_handler.bind())
                await ClockCycles(clock, 10)


if __name__ == "__main__":
    unittest.main()
