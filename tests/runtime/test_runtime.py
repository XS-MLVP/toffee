import asyncio
import unittest

from toffee import (
    ClockCycles,
    DriveStable,
    FallingEdge,
    FSM,
    MemoryBackend,
    RisingEdge,
    Sequence,
    SimTimeout,
    Execution,
    State,
    Value,
    ValueChange,
    Wait,
    Within,
    XEventKind,
    XPhase,
    pytrigger,
    xtrigger,
)
from toffee.backend import BackendCapabilities, RunLimit


class Signal:
    def __init__(self, value=0):
        self.value = value


class Dut:
    def __init__(self):
        self.clk = object()
        self.req = Signal()
        self.ack = Signal()
        self.valid = Signal()
        self.ready = Signal()
        self.flush = Signal()


class LegacyMemoryBackend(MemoryBackend):
    capabilities = BackendCapabilities(half_step=True, stable_sample=True)

    def sample_drive_stable(self):
        raise AssertionError("legacy backend must not be sampled implicitly")


@xtrigger(sample=RisingEdge("clk"))
def handshake(dut):
    return dut.valid & dut.ready & ~dut.flush


@xtrigger(sample=RisingEdge("clk"))
def request_then_ack(dut):
    return Sequence(Wait(dut.req), Within(1, 4, dut.ack))


@xtrigger(sample=RisingEdge("clk"))
def branched_request_ack(dut):
    return FSM(
        start="WAIT_REQ",
        states={
            "WAIT_REQ": State()
            .when(dut.flush)
            .goto("WAIT_REQ")
            .when(dut.req)
            .goto("WAIT_ACK"),
            "WAIT_ACK": State()
            .when(dut.flush)
            .goto("WAIT_REQ")
            .when(dut.ack)
            .trigger("ACK"),
        },
    )


@pytrigger(sample=RisingEdge("clk"))
def python_ready(dut):
    return dut.ready.value == 1


@xtrigger()
def default_sample_handshake(dut):
    return dut.valid & dut.ready


async def wait_for(trigger):
    return await trigger


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_backend_is_reused_across_execution_scopes(self):
        dut = Dut()
        backend = MemoryBackend(dut.clk)

        async with Execution(backend):
            first = await RisingEdge(dut.clk)

        self.assertEqual(backend.watcher_count, 0)
        async with Execution(backend):
            second = await RisingEdge(dut.clk)

        self.assertGreater(second.tick, first.tick)
        backend.close()

    async def test_runtime_reset_rejects_active_watchers(self):
        dut = Dut()
        backend = MemoryBackend(dut.clk)
        handle = backend.arm(RisingEdge(dut.clk))

        with self.assertRaisesRegex(RuntimeError, "active watchers"):
            backend.clear_execution_state()

        self.assertTrue(backend.disarm(handle))
        backend.clear_execution_state()

    async def test_backend_instance_cannot_be_shared_by_executions(self):
        dut = Dut()
        backend = MemoryBackend(dut.clk)
        async with Execution(backend):
            with self.assertRaisesRegex(RuntimeError, "already has"):
                async with Execution(backend):
                    self.fail("shared backend should not enter")

    async def test_memory_backend_honors_wall_clock_quantum(self):
        dut = Dut()
        backend = MemoryBackend(dut.clk)
        backend.arm(ClockCycles(dut.clk, 1000))

        result = backend.run_until(
            RunLimit(
                max_ticks=1000,
                max_wall_time_ms=0.000001,
                budget_check_interval=1,
            )
        )

        self.assertEqual(result.stop_reason, "QUANTUM_EXPIRED")
        self.assertLess(result.advanced_ticks, 1000)

    async def test_falling_barrier_allows_write_before_rising(self):
        dut = Dut()
        observations = []
        backend = MemoryBackend(
            dut.clk,
            on_phase=lambda phase, tick: observations.append(
                (phase, tick, dut.req.value)
            ),
        )
        async with Execution(backend):
            event = await FallingEdge(dut.clk)
            self.assertIs(event.phase, XPhase.FALLING_STABLE)
            dut.req.value = 1
            await RisingEdge(dut.clk)

        self.assertEqual(
            observations,
            [
                (XPhase.FALLING_STABLE, 1, 0),
                (XPhase.RISING_STABLE, 2, 1),
            ],
        )

    async def test_same_edge_is_broadcast_with_same_event_identity(self):
        dut = Dut()
        backend = MemoryBackend(dut.clk)
        trigger = RisingEdge(dut.clk)
        async with Execution(backend):
            first, second = await asyncio.gather(
                wait_for(trigger),
                wait_for(trigger),
            )
        self.assertEqual(first.event_id, second.event_id)
        self.assertIs(first, second)

    async def test_same_drive_phase_is_broadcast_with_same_event_identity(self):
        dut = Dut()
        backend = MemoryBackend(dut.clk)
        async with Execution(backend):
            first, second = await asyncio.gather(
                DriveStable(dut.clk),
                DriveStable(dut.clk),
            )
        self.assertEqual(first.event_id, second.event_id)
        self.assertIs(first, second)
        self.assertIs(first.phase, XPhase.DRIVE_STABLE)

    async def test_backend_without_drive_phase_keeps_legacy_edge_behavior(self):
        dut = Dut()
        async with Execution(LegacyMemoryBackend(dut.clk)):
            await FallingEdge(dut.clk)
            await RisingEdge(dut.clk)

    async def test_backend_without_drive_phase_rejects_it_at_registration(self):
        dut = Dut()
        async with Execution(LegacyMemoryBackend(dut.clk)):
            with self.assertRaisesRegex(RuntimeError, "does not support"):
                await DriveStable(dut.clk)

    async def test_clock_cycles_count_rising_phases(self):
        dut = Dut()
        backend = MemoryBackend(dut.clk)
        async with Execution(backend):
            event = await ClockCycles(dut.clk, 3)
        self.assertEqual(event.kind, XEventKind.CLOCK_CYCLES)
        self.assertEqual(event.tick, 6)

    async def test_value_waits_for_explicit_sample(self):
        dut = Dut()
        dut.ready.value = 1
        backend = MemoryBackend(dut.clk)
        async with Execution(backend):
            event = await Value(
                dut.ready,
                1,
                sample=RisingEdge(dut.clk),
            )
        self.assertEqual(event.tick, 2)
        self.assertIs(event.phase, XPhase.RISING_STABLE)

    async def test_execution_default_sample_is_resolved_at_registration(self):
        dut = Dut()
        dut.ready.value = 1
        backend = MemoryBackend(dut.clk)
        trigger = Value(dut.ready, 1)
        self.assertIsNone(trigger.sample)
        async with Execution(
            backend, default_sample=RisingEdge(dut.clk)
        ):
            event = await trigger
        self.assertEqual(event.tick, 2)
        self.assertIsNone(trigger.sample)

    async def test_condition_without_any_sample_fails_explicitly(self):
        dut = Dut()
        backend = MemoryBackend(dut.clk)
        async with Execution(backend):
            with self.assertRaisesRegex(ValueError, "requires sample"):
                await Value(dut.ready, 1)

    async def test_xtrigger_can_use_execution_default_sample(self):
        dut = Dut()
        dut.valid.value = 1
        dut.ready.value = 1
        backend = MemoryBackend(dut.clk)
        async with Execution(
            backend, default_sample=FallingEdge(dut.clk)
        ):
            event = await default_sample_handshake(dut)
        self.assertEqual(event.tick, 1)
        self.assertIs(event.phase, XPhase.FALLING_STABLE)

    async def test_value_change_uses_registration_baseline_and_sample(self):
        dut = Dut()
        backend = MemoryBackend(dut.clk)
        async with Execution(backend):
            pending = asyncio.create_task(
                wait_for(
                    ValueChange(
                        dut.ready, sample=RisingEdge(dut.clk)
                    )
                )
            )
            await FallingEdge(dut.clk)
            dut.ready.value = 7
            event = await pending

        self.assertIs(event.kind, XEventKind.VALUE_CHANGE)
        self.assertEqual(event.value, 7)
        self.assertEqual(event.tick, 2)

    async def test_condition_change_can_emit_false_transition(self):
        dut = Dut()
        dut.ready.value = 1
        backend = MemoryBackend(dut.clk)
        trigger = Value(
            dut.ready,
            1,
            sample=RisingEdge(dut.clk),
            mode="change",
        )
        handle = backend.arm(trigger)
        entered = backend.run_until(RunLimit(max_ticks=2))
        self.assertEqual(entered.hits[0].value, 1)
        self.assertTrue(backend.rearm(handle))
        dut.ready.value = 0
        left = backend.run_until(RunLimit(max_ticks=2))
        self.assertEqual(left.hits[0].value, 0)

    async def test_sim_timeout_uses_simulation_cycles(self):
        dut = Dut()
        backend = MemoryBackend(dut.clk)
        async with Execution(backend):
            event = await SimTimeout(3, clock=dut.clk)

        self.assertIs(event.kind, XEventKind.TIMEOUT)
        self.assertEqual(event.tick, 6)

    async def test_compiled_expression(self):
        dut = Dut()
        backend = MemoryBackend(dut.clk)
        async with Execution(backend):
            pending = asyncio.create_task(wait_for(handshake(dut)))
            await FallingEdge(dut.clk)
            dut.valid.value = 1
            dut.ready.value = 1
            event = await pending
        self.assertEqual(event.kind, XEventKind.CONDITION)
        self.assertEqual(event.tick, 2)

    async def test_python_predicate_is_sampled(self):
        dut = Dut()
        backend = MemoryBackend(dut.clk)
        async with Execution(backend):
            pending = asyncio.create_task(wait_for(python_ready(dut)))
            await FallingEdge(dut.clk)
            dut.ready.value = 1
            event = await pending
        self.assertEqual(event.kind, XEventKind.CONDITION)
        self.assertEqual(event.tick, 2)

    async def test_sequence_runs_as_stateful_trigger(self):
        dut = Dut()
        backend = MemoryBackend(dut.clk)
        async with Execution(backend):
            pending = asyncio.create_task(wait_for(request_then_ack(dut)))
            await FallingEdge(dut.clk)
            dut.req.value = 1
            await RisingEdge(dut.clk)
            await FallingEdge(dut.clk)
            dut.ack.value = 1
            event = await pending
        self.assertEqual(event.kind, XEventKind.FSM)
        self.assertEqual(event.terminal_state, "MATCHED")
        self.assertEqual(event.tick, 4)

    async def test_explicit_fsm_supports_ordered_branches(self):
        dut = Dut()
        backend = MemoryBackend(dut.clk)
        async with Execution(backend):
            pending = asyncio.create_task(
                wait_for(branched_request_ack(dut))
            )
            await FallingEdge(dut.clk)
            dut.req.value = 1
            await RisingEdge(dut.clk)
            await FallingEdge(dut.clk)
            dut.ack.value = 1
            event = await pending

        self.assertEqual(event.kind, XEventKind.FSM)
        self.assertEqual(event.terminal_state, "ACK")
        self.assertEqual(event.tick, 4)

    async def test_trigger_outside_execution_fails(self):
        dut = Dut()
        with self.assertRaisesRegex(RuntimeError, "active Execution"):
            await RisingEdge(dut.clk)

    async def test_cancel_disarms_watcher_and_reused_slot_gets_generation(self):
        dut = Dut()
        backend = MemoryBackend(dut.clk)
        async with Execution(backend):
            pending = asyncio.create_task(wait_for(RisingEdge(dut.clk)))
            await asyncio.sleep(0)
            first_generation = backend._generations[0]
            pending.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await pending
            self.assertEqual(backend.watcher_count, 0)

            replacement = asyncio.create_task(wait_for(RisingEdge(dut.clk)))
            await asyncio.sleep(0)
            self.assertGreater(backend._generations[0], first_generation)
            await replacement

    async def test_external_asyncio_task_runs_during_simulation(self):
        dut = Dut()
        backend = MemoryBackend(dut.clk)
        heartbeats = 0
        running = True

        async def heartbeat():
            nonlocal heartbeats
            while running:
                heartbeats += 1
                await asyncio.sleep(0)

        async with Execution(backend, max_batch_ticks=1) as execution:
            task = execution.external_task(heartbeat())
            await ClockCycles(dut.clk, 20)
            running = False
            await task

        self.assertGreater(heartbeats, 1)

    async def test_python_boolean_operators_are_rejected(self):
        dut = Dut()

        @xtrigger(sample=RisingEdge("clk"))
        def invalid(expression_dut):
            return expression_dut.valid and expression_dut.ready

        with self.assertRaisesRegex(TypeError, "use '&', '\\|' and '~'"):
            invalid(dut)

    async def test_condition_mode_is_rejected_for_sequence(self):
        dut = Dut()

        @xtrigger(sample=RisingEdge("clk"), mode="change")
        def invalid(expression_dut):
            return Sequence(Wait(expression_dut.req))

        with self.assertRaisesRegex(ValueError, "Sequence/FSM"):
            invalid(dut)
