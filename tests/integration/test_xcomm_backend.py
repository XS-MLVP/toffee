import asyncio
import unittest

from toffee import (
    Bundle,
    ClockCycles,
    FallingEdge,
    FSM,
    Hold,
    LogicValue,
    PackedArray,
    PackedLayout,
    PackedView,
    ReadyValid,
    ReadyValidMonitor,
    RisingEdge,
    RunLimit,
    Role,
    Sequence,
    Execution,
    State,
    SyncDriver,
    Value,
    ValueChange,
    Wait,
    Within,
    XCommClockBackend,
    XEventKind,
    XPhase,
    pytrigger,
    on,
    xtrigger,
    drive_ready_valid,
)

try:
    import xspcomm
except ImportError:
    xspcomm = None


@xtrigger(sample=RisingEdge("clk"))
def native_handshake(dut):
    return dut.valid & dut.ready & ~dut.flush


@xtrigger(sample=RisingEdge("clk"), mode="change")
def native_signal_is_one(dut):
    return dut.signal == 1


WIDE_VALUE = (1 << 100) | 0x123456789ABCDEF


@xtrigger(sample=RisingEdge("clk"))
def native_wide_is_expected(dut):
    return dut.wide == WIDE_VALUE


@xtrigger(sample=RisingEdge("clk"))
def native_wide_signals_match(dut):
    return dut.wide == dut.other_wide


@xtrigger(sample=RisingEdge("clk"))
def native_request_then_ack(dut):
    return Sequence(
        Wait(dut.req),
        Within(1, 4, dut.ack),
        Hold(dut.valid, cycles=2),
    )


@xtrigger(sample=RisingEdge("clk"))
def native_branching_fsm(dut):
    return FSM(
        start="WAIT_REQ",
        states={
            "WAIT_REQ": State().when(dut.req).goto("WAIT_ACK"),
            "WAIT_ACK": State()
            .when(dut.flush)
            .goto("WAIT_REQ")
            .when(dut.ack)
            .trigger("ACK"),
        },
    )


@pytrigger(sample=RisingEdge("clk"))
def native_python_ready(dut):
    dut.predicate_calls += 1
    return dut.predicate_calls >= 3


@unittest.skipIf(
    xspcomm is None
    or not hasattr(xspcomm.XClock, "StepHalf")
    or not hasattr(xspcomm, "XTriggerEngine"),
    "requires xspcomm with the XTriggerEngine API",
)
class XCommBackendTests(unittest.IsolatedAsyncioTestCase):
    async def test_backend_is_reused_and_compiled_cache_resets(self):
        class Dut:
            pass

        dut = Dut()
        dut.clk = xspcomm.XClock(lambda _: 0)
        dut.signal = xspcomm.XData(1, xspcomm.XData.InOut)
        dut.signal.Set(1)
        backend = XCommClockBackend(dut.clk)

        async with Execution(backend):
            first = await native_signal_is_one(dut)
            self.assertEqual(backend.program_cache_size, 1)

        self.assertEqual(backend.program_cache_size, 0)
        self.assertEqual(backend.watcher_count, 0)
        async with Execution(backend):
            second = await native_signal_is_one(dut)

        self.assertGreater(second.tick, first.tick)
        backend.close()

    async def test_ready_valid_fire_lowers_bound_xdata_to_native_expr(self):
        clock = xspcomm.XClock(lambda _: 0)
        valid = xspcomm.XData(1, xspcomm.XData.InOut)
        ready = xspcomm.XData(1, xspcomm.XData.InOut)
        data = xspcomm.XData(8, xspcomm.XData.InOut)
        valid.Set(1)
        ready.Set(1)
        interface = ReadyValid(
            clock,
            valid,
            ready,
            Bundle(data=data),
            role=Role.MONITOR,
        )
        backend = XCommClockBackend(clock)

        async with Execution(backend):
            event = await interface.fire

        self.assertIs(interface.valid, valid)
        self.assertIs(interface.bits.data, data)
        self.assertEqual(event.tick, 1)
        self.assertIs(event.phase, XPhase.DRIVE_STABLE)

    async def test_native_monitor_observes_python_driver_at_drive_stable(self):
        class Input(SyncDriver[dict[str, int]]):
            async def _drive_one(self, request):
                return await drive_ready_valid(
                    clock, valid, ready, lambda: producer.bits.drive(request),
                )

        clock = xspcomm.XClock(lambda _: 0)
        valid = xspcomm.XData(1, xspcomm.XData.InOut)
        ready = xspcomm.XData(1, xspcomm.XData.InOut)
        data = xspcomm.XData(16, xspcomm.XData.InOut)
        ready.Set(1)
        producer = ReadyValid(
            clock,
            valid,
            ready,
            Bundle(data=data),
            role=Role.PRODUCER,
            name="native_loopback",
        )
        backend = XCommClockBackend(clock)
        async with Execution(backend) as execution:
            monitor = ReadyValidMonitor(
                producer.monitor_view()
            ).start(execution)
            driver = Input((valid, data), name=producer.name)
            accepted = await driver.send({"data": 0xBEEF})
            transfer = await monitor.recv()
            driver.close()
            await monitor.aclose()
        self.assertEqual(transfer.value.data.as_int(), 0xBEEF)
        self.assertEqual(transfer.accepted_tick, accepted.tick)

    async def test_native_driver_waits_for_later_same_phase_ready_change(self):
        clock = xspcomm.XClock(lambda _: 0)
        valid = xspcomm.XData(1, xspcomm.XData.InOut)
        ready = xspcomm.XData(1, xspcomm.XData.InOut)
        data = xspcomm.XData(8, xspcomm.XData.InOut)
        ready.Set(1)

        class Input(SyncDriver[int]):
            async def _drive_one(self, request):
                def drive():
                    data.Set(request)
                return await drive_ready_valid(clock, valid, ready, drive)

        async def change_arbitration():
            # Runs after Input's drive on this same falling phase. A provisional
            # ready=1 must not make the driver withdraw an unaccepted request.
            await FallingEdge(clock)
            ready.Set(0)
            await ClockCycles(clock, 2)
            await FallingEdge(clock)
            ready.Set(1)
            return int(clock.GetHalfTick())

        backend = XCommClockBackend(clock)
        driver = Input((valid, data), name="late-ready")
        monitor = None
        async with Execution(backend) as execution:
            monitor = ReadyValidMonitor(ReadyValid(
                clock, valid, ready, Bundle(data=data), role=Role.MONITOR,
            )).start(execution)
            sending = asyncio.create_task(driver.send(0xA5))
            arbitration = asyncio.create_task(change_arbitration())
            try:
                async with asyncio.timeout(2):
                    accepted, released_tick = await asyncio.gather(sending, arbitration)
                    transfer = await monitor.recv()
                self.assertGreater(accepted.tick, released_tick)
                self.assertEqual(transfer.accepted_tick, accepted.tick)
                self.assertEqual(transfer.value.data.as_int(), 0xA5)
            finally:
                sending.cancel()
                arbitration.cancel()
                await asyncio.gather(sending, arbitration, return_exceptions=True)
                driver.close()
                await monitor.aclose()

    async def test_native_condition_change_preserves_false_value(self):
        class Dut:
            pass

        dut = Dut()
        dut.signal = xspcomm.XData(1, xspcomm.XData.InOut)
        dut.clk = xspcomm.XClock(lambda _: 0)
        backend = XCommClockBackend(dut.clk)
        handle = backend.arm(native_signal_is_one(dut))

        self.assertFalse(backend.run_until(RunLimit(max_ticks=2)).hits)
        dut.signal.Set(1)
        entered = backend.run_until(RunLimit(max_ticks=2))
        self.assertIs(entered.hits[0].value, True)
        self.assertTrue(backend.rearm(handle))
        dut.signal.Set(0)
        left = backend.run_until(RunLimit(max_ticks=2))
        self.assertIs(left.hits[0].value, False)
        backend.close()

    async def test_real_xclock_half_step_drives_edge_events(self):
        clock = xspcomm.XClock(lambda _: 0)
        backend = XCommClockBackend(clock)

        async with Execution(backend):
            falling = await FallingEdge(clock)
            self.assertIs(falling.phase, XPhase.FALLING_STABLE)
            self.assertEqual(clock.GetHalfTick(), 1)

            rising = await RisingEdge(clock)
            self.assertIs(rising.phase, XPhase.RISING_STABLE)
            self.assertEqual(clock.GetHalfTick(), 2)

        self.assertEqual(clock.clk, 1)

    async def test_direct_clock_xdata_can_be_edge_source(self):
        clock_data = xspcomm.XData(1, xspcomm.XData.InOut)
        clock = xspcomm.XClock(lambda _: 0)
        clock.Add(clock_data)
        backend = XCommClockBackend(clock)

        async with Execution(backend):
            falling = await FallingEdge(clock_data)
            rising = await RisingEdge(clock_data)

        self.assertEqual(falling.tick, 1)
        self.assertEqual(rising.tick, 2)

    async def test_unregistered_clock_xdata_is_rejected(self):
        clock = xspcomm.XClock(lambda _: 0)
        other = xspcomm.XData(1, xspcomm.XData.InOut)
        backend = XCommClockBackend(clock)

        async with Execution(backend):
            with self.assertRaisesRegex(ValueError, "registered clock pin"):
                await FallingEdge(other)

    async def test_native_engine_broadcasts_one_edge_event(self):
        clock = xspcomm.XClock(lambda _: 0)
        backend = XCommClockBackend(clock)

        async with Execution(backend):
            first, second = await asyncio.gather(
                FallingEdge(clock),
                FallingEdge(clock),
            )

        self.assertIs(first, second)
        self.assertEqual(first.event_id, second.event_id)
        self.assertEqual(first.tick, 1)

    async def test_native_clock_cycles(self):
        clock = xspcomm.XClock(lambda _: 0)
        backend = XCommClockBackend(clock)

        async with Execution(backend, max_batch_ticks=32):
            event = await ClockCycles(clock, 3)

        self.assertIs(event.kind, XEventKind.CLOCK_CYCLES)
        self.assertEqual(event.value, 3)
        self.assertEqual(event.tick, 6)

    async def test_native_value_equality_uses_requested_sample_phase(self):
        clock = xspcomm.XClock(lambda _: 0)
        ready = xspcomm.XData(1, xspcomm.XData.InOut)
        ready.Set(1)
        backend = XCommClockBackend(clock)

        async with Execution(backend, max_batch_ticks=32):
            event = await Value(ready, 1, sample=RisingEdge(clock))

        self.assertIs(event.kind, XEventKind.VALUE)
        self.assertIs(event.source, ready)
        self.assertEqual(event.value, 1)
        self.assertEqual(event.tick, 2)

    async def test_native_value_accepts_direct_xdata(self):
        clock = xspcomm.XClock(lambda _: 0)
        data = xspcomm.XData(1, xspcomm.XData.InOut)
        data.Set(1)
        backend = XCommClockBackend(clock)

        async with Execution(backend, max_batch_ticks=32):
            event = await Value(data, 1, sample=RisingEdge(clock))

        self.assertIs(event.source, data)
        self.assertEqual(event.value, 1)
        self.assertEqual(event.tick, 2)

    async def test_native_value_change_uses_arm_baseline(self):
        data = xspcomm.XData(8, xspcomm.XData.InOut)
        clock = xspcomm.XClock(lambda _: 0)
        backend = XCommClockBackend(clock)

        async with Execution(backend, max_batch_ticks=32):
            pending = asyncio.create_task(
                ValueChange(data, sample=RisingEdge(clock))._wait()
            )
            await FallingEdge(clock)
            data.Set(9)
            event = await pending

        self.assertIs(event.kind, XEventKind.VALUE_CHANGE)
        self.assertEqual(event.value, 9)
        self.assertEqual(event.tick, 2)

    async def test_native_value_change_preserves_unknown_mask(self):
        data = xspcomm.XData(4, xspcomm.XData.InOut)
        clock = xspcomm.XClock(lambda _: 0)
        backend = XCommClockBackend(clock)

        async with Execution(backend, max_batch_ticks=32):
            pending = asyncio.create_task(
                ValueChange(data, sample=RisingEdge(clock))._wait()
            )
            await FallingEdge(clock)
            data.Set("0b00x0")
            event = await pending

        self.assertIsInstance(event.value, LogicValue)
        self.assertEqual(event.value.value, 4)
        self.assertEqual(event.value.x_mask, 4)
        self.assertEqual(event.value.width, 4)

    async def test_native_value_rejects_out_of_range_expected(self):
        data = xspcomm.XData(4, xspcomm.XData.InOut)
        clock = xspcomm.XClock(lambda _: 0)
        backend = XCommClockBackend(clock)

        async with Execution(backend):
            with self.assertRaisesRegex(ValueError, "does not fit"):
                await Value(data, 16, sample=RisingEdge(clock))

    async def test_native_value_supports_wide_signal(self):
        data = xspcomm.XData(128, xspcomm.XData.InOut)
        data.Set(WIDE_VALUE)
        clock = xspcomm.XClock(lambda _: 0)
        backend = XCommClockBackend(clock)

        async with Execution(backend):
            event = await Value(
                data, WIDE_VALUE, sample=RisingEdge(clock)
            )

        self.assertEqual(event.value, WIDE_VALUE)

    async def test_native_value_change_supports_wide_signal(self):
        data = xspcomm.XData(128, xspcomm.XData.InOut)
        clock = xspcomm.XClock(lambda _: 0)
        backend = XCommClockBackend(clock)

        async with Execution(backend):
            pending = asyncio.create_task(
                ValueChange(data, sample=RisingEdge(clock))._wait()
            )
            await FallingEdge(clock)
            data.Set(WIDE_VALUE)
            event = await pending

        self.assertEqual(event.value, WIDE_VALUE)

    async def test_xtrigger_compares_wide_constant_and_signal_natively(self):
        class Dut:
            pass

        dut = Dut()
        dut.clk = xspcomm.XClock(lambda _: 0)
        dut.wide = xspcomm.XData(128, xspcomm.XData.InOut)
        dut.other_wide = xspcomm.XData(128, xspcomm.XData.InOut)
        dut.wide.Set(WIDE_VALUE)
        dut.other_wide.Set(WIDE_VALUE)
        backend = XCommClockBackend(dut.clk)

        async with Execution(backend):
            constant_event = await native_wide_is_expected(dut)
            signal_event = await native_wide_signals_match(dut)

        self.assertTrue(constant_event.value)
        self.assertTrue(signal_event.value)

    async def test_packed_array_splits_one_bus_into_live_xdata_lanes(self):
        bus = xspcomm.XData(128, xspcomm.XData.InOut)
        lanes = PackedArray(bus, 32, name="lanes")
        lanes[0].Set(0x11223344)
        lanes[3].Set(0xAABBCCDD)

        self.assertIs(lanes.parent, bus)
        self.assertEqual(len(lanes), 4)
        self.assertEqual(
            bus.U(), (0xAABBCCDD << 96) | 0x11223344
        )
        self.assertEqual(lanes[3].U(), 0xAABBCCDD)
        self.assertEqual(
            int.from_bytes(bus.GetBytes(), "little"),
            (0xAABBCCDD << 96) | 0x11223344,
        )

        msb_first = PackedArray(bus, 32, lsb_first=False)
        self.assertEqual(msb_first[0].U(), 0xAABBCCDD)

    async def test_packed_view_supports_array_of_nested_structs(self):
        entry = PackedLayout.struct(
            {
                "valid": PackedLayout.bits(1),
                "tag": PackedLayout.bits(7),
                "data": PackedLayout.array(2, PackedLayout.bits(32)),
            }
        )
        bus = xspcomm.XData(144, xspcomm.XData.InOut)
        entries = PackedView(
            bus, PackedLayout.array(2, entry), name="entries"
        )

        entries[1].drive(
            {"valid": 1, "tag": 0x35, "data": [0x11223344, 0xAABBCCDD]}
        )

        packed_entry = (
            1
            | (0x35 << 1)
            | (0x11223344 << 8)
            | (0xAABBCCDD << 40)
        )
        self.assertEqual(bus.U(), packed_entry << 72)
        self.assertEqual(entries[1].tag.U(), 0x35)
        self.assertEqual(entries[1].data[1].U(), 0xAABBCCDD)
        self.assertEqual(
            tuple(path for path, _ in entries.leaves()),
            (
                "entries[0].valid",
                "entries[0].tag",
                "entries[0].data[0]",
                "entries[0].data[1]",
                "entries[1].valid",
                "entries[1].tag",
                "entries[1].data[0]",
                "entries[1].data[1]",
            ),
        )
        snapshot = entries.sample()
        self.assertEqual(snapshot[1].tag.as_int(), 0x35)
        self.assertEqual(snapshot[1].data[0].as_int(), 0x11223344)
        bundled = Bundle(entries=entries).sample()
        self.assertEqual(bundled.entries[1].data[1].as_int(), 0xAABBCCDD)

    async def test_packed_view_supports_multidimensional_order(self):
        bus = xspcomm.XData(48, xspcomm.XData.InOut)
        matrix = PackedView(
            bus,
            PackedLayout.array(
                2,
                PackedLayout.array(3, PackedLayout.bits(8)),
                lsb_first=False,
            ),
            name="matrix",
        )

        matrix[0][0].Set(0xAA)
        matrix[1][2].Set(0x55)

        self.assertEqual(bus.U(), (0xAA << 24) | (0x55 << 16))
        self.assertEqual(matrix[0][0].U(), 0xAA)
        self.assertEqual(matrix[1][2].U(), 0x55)

        padded_bus = xspcomm.XData(40, xspcomm.XData.InOut)
        padded = PackedView(
            padded_bus,
            PackedLayout.array(3, PackedLayout.bits(8), stride=16),
        )
        padded.drive([0x11, 0x22, 0x33])
        self.assertEqual(padded_bus.U(), 0x33_00_22_00_11)

        ordered_bus = xspcomm.XData(16, xspcomm.XData.InOut)
        ordered = PackedView(
            ordered_bus,
            PackedLayout.struct(
                {"first": PackedLayout.bits(8), "second": PackedLayout.bits(8)},
                lsb_first=False,
            ),
        )
        ordered.drive({"first": 0xAA, "second": 0x55})
        self.assertEqual(ordered_bus.U(), 0xAA55)

    async def test_packed_view_supports_explicit_offsets_and_padding(self):
        layout = PackedLayout.struct(
            {
                "opcode": PackedLayout.bits(6, offset=0),
                "tag": PackedLayout.bits(8, offset=8),
                "payload": PackedLayout.bits(64, offset=32),
            },
            width=128,
        )
        bus = xspcomm.XData(128, xspcomm.XData.InOut)
        packet = PackedView(bus, layout, name="packet")

        packet.drive(
            {"opcode": 0x2A, "tag": 0x5C, "payload": 0x123456789ABCDEF0}
        )

        self.assertEqual(
            bus.U(),
            0x2A | (0x5C << 8) | (0x123456789ABCDEF0 << 32),
        )
        with self.assertRaisesRegex(ValueError, "mix explicit and automatic"):
            PackedLayout.struct(
                {
                    "placed": PackedLayout.bits(4, offset=0),
                    "automatic": PackedLayout.bits(4),
                }
            )
        with self.assertRaisesRegex(ValueError, "overlap"):
            PackedLayout.struct(
                {
                    "a": PackedLayout.bits(8, offset=0),
                    "b": PackedLayout.bits(8, offset=4),
                },
                width=16,
            )
        alias_layout = PackedLayout.struct(
            {
                "whole": PackedLayout.bits(8, offset=0),
                "low": PackedLayout.bits(4, offset=0),
            },
            width=8,
            allow_overlap=True,
        )
        alias = PackedView(
            xspcomm.XData(8, xspcomm.XData.InOut), alias_layout
        )
        alias.whole.Set(0xAB)
        self.assertEqual(alias.low.U(), 0xB)
        with self.assertRaisesRegex(ValueError, "overlapping fields"):
            alias.drive({"whole": 0x12, "low": 3})

    async def test_native_capacity_failure_is_explicit(self):
        clock = xspcomm.XClock(lambda _: 0)
        backend = XCommClockBackend(clock, capacity=1)

        async with Execution(backend) as execution:
            async with execution.paused():
                first = asyncio.create_task(FallingEdge(clock)._wait())
                await asyncio.sleep(0)
                with self.assertRaisesRegex(RuntimeError, "capacity exhausted"):
                    await RisingEdge(clock)
            await first

    async def test_xtrigger_expression_is_lowered_to_native_engine(self):
        class Dut:
            pass

        dut = Dut()
        dut.valid = xspcomm.XData(1, xspcomm.XData.InOut)
        dut.ready = xspcomm.XData(1, xspcomm.XData.InOut)
        dut.flush = xspcomm.XData(1, xspcomm.XData.InOut)
        dut.valid.Set(1)
        dut.ready.Set(1)
        dut.flush.Set(0)
        dut.clk = xspcomm.XClock(lambda _: 0)
        backend = XCommClockBackend(dut.clk)

        async with Execution(backend, max_batch_ticks=32):
            event = await native_handshake(dut)

        self.assertIs(event.kind, XEventKind.CONDITION)
        self.assertEqual(event.source, "native_handshake")
        self.assertEqual(event.tick, 2)

    async def test_xtrigger_uses_direct_dut_xdata(self):
        class Dut:
            pass

        dut = Dut()
        dut.clk = xspcomm.XClock(lambda _: 0)
        dut.valid = xspcomm.XData(1, xspcomm.XData.InOut)
        dut.ready = xspcomm.XData(1, xspcomm.XData.InOut)
        dut.flush = xspcomm.XData(1, xspcomm.XData.InOut)
        dut.valid.Set(1)
        dut.ready.Set(1)
        dut.flush.Set(0)
        backend = XCommClockBackend(dut.clk)

        async with Execution(backend, max_batch_ticks=32):
            result = await native_handshake(dut)

        self.assertIs(result.kind, XEventKind.CONDITION)
        self.assertEqual(result.tick, 2)

    async def test_identical_xtrigger_reuses_native_program(self):
        class Dut:
            pass

        dut = Dut()
        dut.valid = xspcomm.XData(1, xspcomm.XData.InOut)
        dut.ready = xspcomm.XData(1, xspcomm.XData.InOut)
        dut.flush = xspcomm.XData(1, xspcomm.XData.InOut)
        dut.valid.Set(1)
        dut.ready.Set(1)
        dut.clk = xspcomm.XClock(lambda _: 0)
        backend = XCommClockBackend(dut.clk)

        async with Execution(backend):
            await native_handshake(dut)
            await native_handshake(dut)
            self.assertEqual(backend.program_cache_size, 1)

    async def test_xtrigger_sequence_runs_as_native_fsm(self):
        class Dut:
            pass

        dut = Dut()
        dut.req = xspcomm.XData(1, xspcomm.XData.InOut)
        dut.ack = xspcomm.XData(1, xspcomm.XData.InOut)
        dut.valid = xspcomm.XData(1, xspcomm.XData.InOut)
        rising_count = 0

        def drive(rising):
            nonlocal rising_count
            if rising:
                rising_count += 1
                if rising_count == 1:
                    dut.req.Set(1)
                if rising_count == 2:
                    dut.ack.Set(1)
                if rising_count >= 3:
                    dut.valid.Set(1)
            return 0

        dut.clk = xspcomm.XClock(drive)
        backend = XCommClockBackend(dut.clk)

        async with Execution(backend, max_batch_ticks=32):
            event = await native_request_then_ack(dut)

        self.assertIs(event.kind, XEventKind.FSM)
        self.assertEqual(event.terminal_state, "MATCHED")
        self.assertEqual(event.tick, 8)

    async def test_explicit_fsm_is_lowered_to_native_transitions(self):
        class Dut:
            pass

        dut = Dut()
        dut.req = xspcomm.XData(1, xspcomm.XData.InOut)
        dut.ack = xspcomm.XData(1, xspcomm.XData.InOut)
        dut.flush = xspcomm.XData(1, xspcomm.XData.InOut)
        rising_count = 0

        def drive(rising):
            nonlocal rising_count
            if rising:
                rising_count += 1
                if rising_count == 1:
                    dut.req.Set(1)
                if rising_count == 2:
                    dut.ack.Set(1)
            return 0

        dut.clk = xspcomm.XClock(drive)
        backend = XCommClockBackend(dut.clk)

        async with Execution(backend, max_batch_ticks=32):
            event = await native_branching_fsm(dut)

        self.assertIs(event.kind, XEventKind.FSM)
        self.assertEqual(event.terminal_state, "ACK")
        self.assertEqual(event.tick, 4)

    async def test_pytrigger_rearms_at_each_native_sample(self):
        class Dut:
            pass

        dut = Dut()
        dut.predicate_calls = 0
        dut.clk = xspcomm.XClock(lambda _: 0)
        backend = XCommClockBackend(dut.clk)

        async with Execution(backend, max_batch_ticks=64):
            event = await native_python_ready(dut)

        self.assertIs(event.kind, XEventKind.CONDITION)
        self.assertEqual(event.tick, 6)
        self.assertEqual(dut.predicate_calls, 3)

    async def test_on_uses_native_rearm_without_registration_gap(self):
        clock = xspcomm.XClock(lambda _: 0)
        received = []
        ready = asyncio.Event()

        @on(FallingEdge(clock), capacity=8)
        async def observe(event):
            received.append(event.tick)
            if len(received) == 3:
                ready.set()

        backend = XCommClockBackend(clock)
        async with Execution(backend) as execution:
            subscription = execution.subscribe(observe.bind())
            await ready.wait()
            execution.reactor.cancel_subscription(subscription)

        self.assertEqual(received[:3], [1, 3, 5])
