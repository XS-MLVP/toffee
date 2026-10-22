"""Native asyncio handoffs settle before simulation phases advance."""
import asyncio
import importlib.util
import unittest
from contextvars import ContextVar
from toffee import Execution, MemoryBackend, FallingEdge, ClockCycles, DriveStable, on

class AsyncioObservationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        from toffee._asyncio_observer import _observers
        self.assertNotIn(asyncio.get_running_loop(), _observers)

    def environment(self):
        clock = object()
        backend = MemoryBackend(clock)
        self.addCleanup(backend.close)
        return clock, backend

    async def test_native_lock_queue_gather_shield_before_drive_stable(self):
        clock, backend = self.environment()
        trace, captures = [], []
        async with Execution(backend) as execution:
            locks = [asyncio.Lock() for _ in range(8)]
            queues = [asyncio.Queue() for _ in range(8)]
            for lock in locks:
                await lock.acquire()
            async def lock_relay(i):
                async with locks[i]:
                    trace.append(('lock', i, backend.tick))
                    if i + 1 < 8:
                        locks[i + 1].release()
            async def queue_relay(i):
                await queues[i].get()
                trace.append(('queue', i, backend.tick))
                if i + 1 < 8:
                    queues[i + 1].put_nowait(i)
            calls = [asyncio.create_task(lock_relay(i)) for i in range(8)]
            calls += [asyncio.create_task(queue_relay(i)) for i in range(8)]
            async def joined():
                await asyncio.shield(asyncio.shield(asyncio.gather(*calls)))
                trace.append(('joined', 0, backend.tick))
            joining = asyncio.create_task(joined())
            @on(DriveStable(clock), capture=lambda _: (backend.tick, len(trace)))
            async def record(item):
                captures.append(item)
            execution.subscribe(record.bind())
            await FallingEdge(clock)
            locks[0].release()
            queues[0].put_nowait(1)
            await ClockCycles(clock, 10)
            await joining
            self.assertEqual({row[2] for row in trace}, {1})
            self.assertEqual(captures[0], (1, 17))

    async def test_new_task_and_sleep_zero_chain_precede_clock_batch(self):
        clock, backend = self.environment()
        trace = []
        async with Execution(backend):
            async def chain():
                for _ in range(32):
                    await asyncio.sleep(0)
                trace.append(backend.tick)
            task = asyncio.create_task(chain())
            await ClockCycles(clock, 100)
            await task
        self.assertEqual(trace, [0])

    async def test_raw_callback_chain_and_cancelled_native_handle(self):
        clock, backend = self.environment()
        trace = []
        loop = asyncio.get_running_loop()
        async with Execution(backend):
            def chain(depth):
                if depth:
                    loop.call_soon(chain, depth - 1)
                else:
                    trace.append(backend.tick)
            await FallingEdge(clock)
            cancelled = loop.call_soon(trace.append, 'cancelled')
            self.assertIs(type(cancelled), asyncio.Handle)
            cancelled.cancel()
            loop.call_soon(chain, 32)
            await ClockCycles(clock, 10)
        self.assertEqual(trace, [1])

    async def test_cancelled_only_callback_does_not_hold_next_clock_step(self):
        clock, backend = self.environment()
        async with Execution(backend):
            await FallingEdge(clock)
            handle = asyncio.get_running_loop().call_soon(self.fail, "cancelled callback ran")
            handle.cancel()
            event = await ClockCycles(clock, 1)
            self.assertEqual(event.tick, 2)

    async def test_scheduling_failure_does_not_leave_pending_observation(self):
        clock, backend = self.environment()
        loop = asyncio.get_running_loop()
        original, reject = loop.call_soon, False
        failure = MemoryError("callback scheduling failed")

        def call_soon(callback, *args, context=None):
            if reject:
                raise failure
            return original(callback, *args, context=context)

        loop.call_soon = call_soon
        try:
            async with Execution(backend):
                reject = True
                try:
                    with self.assertRaises(MemoryError) as caught:
                        loop.call_soon(self.fail, "rejected callback ran")
                    self.assertIs(caught.exception, failure)
                finally:
                    reject = False
                self.assertEqual((await ClockCycles(clock, 1)).tick, 2)
        finally:
            loop.call_soon = original

    async def test_pending_external_future_does_not_hold_clock(self):
        clock, backend = self.environment()
        gate = asyncio.Event()
        async with Execution(backend):
            task = asyncio.create_task(gate.wait())
            try:
                await ClockCycles(clock, 10)
                self.assertEqual(backend.tick, 20)
                self.assertFalse(task.done())
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_external_busy_task_is_native_and_survives_exit(self):
        clock, backend = self.environment()
        async def busy():
            while True:
                await asyncio.sleep(0)
        async with Execution(backend) as execution:
            task = execution.external_task(busy())
            self.assertIs(type(task), asyncio.Task)
            await ClockCycles(clock, 10)
            self.assertEqual(backend.tick, 20)
        try:
            self.assertFalse(task.done())
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_external_child_task_does_not_inherit_execution(self):
        clock, backend = self.environment()
        children = []
        async def busy():
            while True:
                await asyncio.sleep(0)
        async def library():
            child = asyncio.create_task(busy())
            children.append(child)
            return 42
        try:
            async with Execution(backend) as execution:
                self.assertEqual(await execution.external_task(library()), 42)
                await ClockCycles(clock, 10)
                self.assertEqual(backend.tick, 20)
        finally:
            for child in children:
                child.cancel()
            await asyncio.gather(*children, return_exceptions=True)

    async def test_cancelled_task_finally_writes_before_next_phase(self):
        clock, backend = self.environment()
        trace = []
        async with Execution(backend):
            async def worker():
                try:
                    await asyncio.Event().wait()
                finally:
                    trace.append(backend.tick)
            task = asyncio.create_task(worker())
            await FallingEdge(clock)
            task.cancel()
            await ClockCycles(clock, 10)
            await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(trace, [1])

    async def test_shared_observer_independent_domains(self):
        clock_a, backend_a = self.environment()
        clock_b, backend_b = self.environment()
        loop = asyncio.get_running_loop()
        original = loop.call_soon
        async def first():
            async with Execution(backend_a) as a:
                async def work():
                    for _ in range(40):
                        await asyncio.sleep(0)
                task = asyncio.create_task(work())
                await ClockCycles(clock_a, 2)
                await task
                return a._domain.observer
        async def second():
            async with Execution(backend_b) as b:
                await ClockCycles(clock_b, 20)
                self.assertEqual(backend_a.tick, 0)
                self.assertEqual(len(b._domain.observer.users), 2)
                return b._domain.observer
        one, two = await asyncio.gather(first(), second())
        self.assertIs(one, two)
        self.assertEqual(loop.call_soon, original)

    async def test_existing_call_soon_wrapper_restored(self):
        clock, backend = self.environment()
        loop = asyncio.get_running_loop()
        previous = loop.call_soon
        seen = []
        def installed(callback, *args, context=None):
            seen.append(True)
            return previous(callback, *args, context=context)
        loop.call_soon = installed
        try:
            async with Execution(backend):
                await ClockCycles(clock, 2)
            self.assertIs(loop.call_soon, installed)
            self.assertTrue(seen)
        finally:
            delattr(loop, 'call_soon')

    async def test_task_factory_unchanged(self):
        clock, backend = self.environment()
        loop = asyncio.get_running_loop()
        before = loop.get_task_factory()
        async with Execution(backend):
            self.assertIs(loop.get_task_factory(), before)
            await ClockCycles(clock, 1)
        self.assertIs(loop.get_task_factory(), before)

    async def test_zero_time_loop_fails_without_advancing_clock(self):
        clock, backend = self.environment()
        task = None
        async def busy():
            while True:
                await asyncio.sleep(0)
        try:
            from toffee import SimulationNotSettledError
            with self.assertRaisesRegex(SimulationNotSettledError, "tick=0"):
                async with Execution(backend, max_settle_rounds=16) as execution:
                    task = asyncio.create_task(busy())
                    await ClockCycles(clock, 10)
            self.assertEqual(backend.tick, 0)
        finally:
            if task:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_timer_admission_wakes_domain_task(self):
        clock, backend = self.environment()
        loop = asyncio.get_running_loop()
        async with Execution(backend):
            future = loop.create_future()
            loop.call_later(0.001, future.set_result, 7)
            # No clock waiter exists: admit external completion, then drive.
            self.assertEqual(await future, 7)
            await ClockCycles(clock, 1)
            self.assertEqual(backend.tick, 2)

    async def test_thread_result_wakes_domain_task(self):
        clock, backend = self.environment()
        async with Execution(backend) as execution:
            result = await execution.external_task(asyncio.to_thread(lambda: 9))
            self.assertEqual(result, 9)
            await ClockCycles(clock, 1)
            self.assertEqual(backend.tick, 2)

    async def test_external_cancellation_restores_observer_and_backend(self):
        clock, backend = self.environment()
        entered = asyncio.Event()
        loop = asyncio.get_running_loop()
        original = loop.call_soon
        async def run():
            async with Execution(backend):
                entered.set()
                await asyncio.Event().wait()
        task = asyncio.create_task(run())
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(loop.call_soon, original)
        self.assertEqual(backend.watcher_count, 0)
        self.assertIsNone(backend._owner)

    @unittest.skipUnless(hasattr(asyncio, "eager_task_factory"), "requires Python 3.12")
    async def test_eager_task_factory_remains_installed(self):
        clock, backend = self.environment()
        loop = asyncio.get_running_loop()
        previous = loop.get_task_factory()
        trace = []
        loop.set_task_factory(asyncio.eager_task_factory)
        try:
            async with Execution(backend):
                async def eager():
                    trace.append(backend.tick)
                    await asyncio.sleep(0)
                    trace.append(backend.tick)
                task = asyncio.create_task(eager())
                await ClockCycles(clock, 2)
                await task
                self.assertIs(loop.get_task_factory(), asyncio.eager_task_factory)
            self.assertEqual(trace, [0, 0])
        finally:
            loop.set_task_factory(previous)

    async def test_capture_created_task_belongs_to_execution(self):
        clock, backend = self.environment()
        trace, tasks = [], []
        async with Execution(backend) as execution:
            async def child():
                for _ in range(8):
                    await asyncio.sleep(0)
                trace.append(backend.tick)

            def capture(event):
                if not tasks:
                    tasks.append(asyncio.create_task(child()))
                return event

            @on(FallingEdge(clock), capture=capture)
            async def observe(event):
                pass

            execution.subscribe(observe.bind())
            await ClockCycles(clock, 10)
            await asyncio.gather(*tasks)
            self.assertEqual(trace, [1])

    async def test_invalid_callbacks_keep_debug_validation(self):
        clock, backend = self.environment()
        loop = asyncio.get_running_loop()
        before = loop.get_debug()
        loop.set_debug(True)
        async def coroutine():
            pass
        instance = coroutine()
        try:
            async with Execution(backend):
                for callback in (None, coroutine, instance):
                    with self.assertRaises(TypeError):
                        loop.call_soon(callback)
                await ClockCycles(clock, 1)
        finally:
            instance.close()
            loop.set_debug(before)

    async def test_callback_failure_preserves_host_exception_handler(self):
        clock, backend = self.environment()
        loop = asyncio.get_running_loop()
        previous = loop.get_exception_handler()
        errors = []
        def report(loop, context):
            errors.append(context)
        error = ValueError("callback failed")
        def broken():
            raise error
        loop.set_exception_handler(report)
        try:
            async with Execution(backend):
                loop.call_soon(broken)
                await ClockCycles(clock, 1)
                self.assertIs(loop.get_exception_handler(), report)
            self.assertIs(errors[0]["exception"], error)
            self.assertIn("broken", errors[0]["message"])
        finally:
            loop.set_exception_handler(previous)

    async def test_failed_task_factory_releases_entered_resources(self):
        clock, backend = self.environment()
        loop = asyncio.get_running_loop()
        original, factory = loop.call_soon, loop.get_task_factory()
        def reject(loop, coro, **kwargs):
            raise ValueError("factory rejected pump")
        loop.set_task_factory(reject)
        try:
            with self.assertRaisesRegex(ValueError, "factory rejected pump"):
                async with Execution(backend):
                    self.fail("entry must fail")
        finally:
            loop.set_task_factory(factory)
        self.assertEqual(loop.call_soon, original)
        self.assertIsNone(backend._owner)
        async with Execution(backend):
            await ClockCycles(clock, 1)

    async def test_replaced_hook_fails_without_overwriting_host_change(self):
        clock, backend = self.environment()
        loop = asyncio.get_running_loop()
        original = loop.call_soon
        replacement = None
        try:
            with self.assertRaisesRegex(RuntimeError, "call_soon changed"):
                async with Execution(backend):
                    observer_hook = loop.call_soon
                    def replacement(callback, *args, context=None):
                        return observer_hook(callback, *args, context=context)
                    loop.call_soon = replacement
                    await ClockCycles(clock, 1)
            self.assertIs(loop.call_soon, replacement)
            # The old observer may still be inside the host's wrapper. It must
            # remain passive when a new Execution installs a new observer.
            async with Execution(backend):
                await ClockCycles(clock, 1)
            self.assertIs(loop.call_soon, replacement)
        finally:
            loop.call_soon = original

    async def test_external_task_clears_reactor_but_keeps_user_context(self):
        clock, backend = self.environment()
        marker = ContextVar("user_marker", default="missing")
        token = marker.set("kept")
        try:
            async with Execution(backend) as execution:
                async def external():
                    with self.assertRaisesRegex(RuntimeError, "active Execution"):
                        await ClockCycles(clock, 1)
                    return marker.get()
                task = execution.external_task(external(), name="host-operation")
                self.assertEqual(task.get_name(), "host-operation")
                self.assertEqual(await task, "kept")
                self.assertEqual(backend.tick, 0)
        finally:
            marker.reset(token)

    async def test_execution_does_not_cancel_user_owned_domain_task(self):
        clock, backend = self.environment()
        gate = asyncio.Event()
        async with Execution(backend):
            task = asyncio.create_task(gate.wait())
            await ClockCycles(clock, 1)
        try:
            self.assertFalse(task.done())
            gate.set()
            self.assertTrue(await task)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_task_group_failure_preserves_exception_and_cleanup(self):
        clock, backend = self.environment()
        error = ValueError("input failed")
        with self.assertRaises(ExceptionGroup) as raised:
            async with Execution(backend):
                async def failed():
                    await FallingEdge(clock)
                    raise error
                async def waiting():
                    await ClockCycles(clock, 100)
                async with asyncio.TaskGroup() as group:
                    group.create_task(failed())
                    group.create_task(waiting())
        self.assertIs(raised.exception.exceptions[0], error)
        self.assertEqual(backend.watcher_count, 0)
        self.assertIsNone(backend._owner)


@unittest.skipUnless(importlib.util.find_spec("xspcomm"), "requires native xspcomm")
class NativeAsyncioObservationTests(AsyncioObservationTests):
    def environment(self):
        import xspcomm
        from toffee import XCommClockBackend
        clock = xspcomm.XClock(lambda _: 0)
        backend = XCommClockBackend(clock)
        self.addCleanup(backend.close)
        return clock, backend

if __name__ == '__main__':
    unittest.main(verbosity=2)
