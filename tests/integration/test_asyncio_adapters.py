import asyncio
import unittest

from toffee import (
    AllOf,
    AnyOf,
    AsyncioEventTrigger,
    FallingEdge,
    MemoryBackend,
    QueueTrigger,
    RisingEdge,
    Execution,
    TaskComplete,
    WallTimeout,
    XEventKind,
)


class AsyncioAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_queue_trigger_carries_payload_without_execution(self):
        queue = asyncio.Queue()
        queue.put_nowait({"command": "stop"})

        event = await QueueTrigger(queue)

        self.assertIs(event.kind, XEventKind.EXTERNAL)
        self.assertEqual(event.value, {"command": "stop"})

    async def test_anyof_external_winner_cancels_execution_registration(self):
        clock = object()
        backend = MemoryBackend(clock)
        stop = asyncio.Event()

        async with Execution(backend) as execution:
            async with execution.paused():
                stop.set()
                winner = await AnyOf(
                    FallingEdge(clock), AsyncioEventTrigger(stop)
                )
                await asyncio.sleep(0)
                self.assertEqual(backend.watcher_count, 0)

        self.assertIs(winner.kind, XEventKind.EXTERNAL)

    async def test_allof_preserves_argument_order_and_causes(self):
        clock = object()
        backend = MemoryBackend(clock)

        async with Execution(backend):
            combined = await AllOf(
                FallingEdge(clock), RisingEdge(clock)
            )

        self.assertIs(combined.kind, XEventKind.COMPOSITE)
        self.assertEqual([event.tick for event in combined.causes], [1, 2])

    async def test_anyof_loser_does_not_cancel_wrapped_external_task(self):
        stop = asyncio.Event()
        stop.set()

        async def external_work():
            await asyncio.sleep(0)
            return 42

        task = asyncio.create_task(external_work())
        winner = await AnyOf(
            AsyncioEventTrigger(stop), TaskComplete(task)
        )

        self.assertIs(winner.kind, XEventKind.EXTERNAL)
        self.assertEqual(await task, 42)

    async def test_wall_timeout_is_external_time(self):
        event = await WallTimeout(0)
        self.assertIs(event.kind, XEventKind.TIMEOUT)
        self.assertEqual(event.tick, -1)


if __name__ == "__main__":
    unittest.main()
