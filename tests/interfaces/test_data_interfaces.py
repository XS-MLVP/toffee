import asyncio
import unittest

from toffee import (
    Bundle,
    BundleValue,
    ClockCycles,
    Driver,
    Field,
    MemoryBackend,
    Monitor,
    ReadyValid,
    ReadyValidMonitor,
    Role,
    Execution,
    SyncDriver,
    XPhase,
    as_xdata,
    drive_ready_valid,
)


class BundleInput(SyncDriver[dict[str, int]]):
    """Test-local protocol composition using the common input scheduler."""

    def __init__(self, interface):
        self.interface = interface
        super().__init__(
            (interface.valid, *(signal for _, signal in interface.bits.leaves())),
            name=interface.name,
        )

    async def _drive_one(self, request):
        interface = self.interface
        return await drive_ready_valid(
            interface.clock, interface.valid, interface.ready,
            lambda: interface.bits.drive(request),
        )


class Signal:
    def __init__(self, value=0, *, width=8, x_mask=0, name=""):
        self.value = value
        self.width = width
        self.x_mask = x_mask
        self.mName = name

    def W(self):
        return self.width

    def U(self):
        return self.value

    def XMask(self):
        return self.x_mask

    def Set(self, value):
        self.value = value


class DirectionalSignal(Signal):
    def __init__(self, direction, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.direction = direction

    def IsInIO(self):
        return self.direction == "in"

    def IsOutIO(self):
        return self.direction == "out"

    def IsBiIO(self):
        return self.direction == "inout"


class NativeProxySignal(Signal):
    def __init__(self, native_address, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.native_address = native_address

    def CSelf(self):
        return self.native_address


class BundleTests(unittest.TestCase):
    def test_direct_signal_preserves_identity(self):
        data = Signal(3)
        bundle = Bundle(value=data)
        self.assertIs(as_xdata(data), data)
        self.assertIs(bundle.value, data)

    def test_nested_snapshot_is_immutable_and_preserves_xz(self):
        addr = Signal(0x12, width=8)
        data = Signal(0xA5, width=8, x_mask=0x10)
        bundle = Bundle(
            header={"addr": addr},
            lanes=[data, Signal(7, width=4)],
        )
        snapshot = bundle.sample()
        addr.value = 0x34
        data.value = 0
        self.assertIsInstance(snapshot, BundleValue)
        self.assertEqual(snapshot.header.addr.as_int(), 0x12)
        self.assertEqual(snapshot.lanes[0].value, 0xA5)
        self.assertEqual(snapshot.lanes[0].x_mask, 0x10)
        with self.assertRaisesRegex(ValueError, "X/Z"):
            snapshot.lanes[0].as_int()
        self.assertEqual(snapshot.lanes[0].as_int(strict=False), 0xA5)

    def test_drive_requires_exact_shape(self):
        addr = Signal()
        lanes = [Signal(), Signal()]
        bundle = Bundle(addr=addr, lanes=lanes)
        bundle.drive({"addr": 4, "lanes": [5, 6]})
        self.assertEqual((addr.value, lanes[0].value, lanes[1].value), (4, 5, 6))
        with self.assertRaisesRegex(ValueError, "shape mismatch"):
            bundle.drive({"addr": 1})

    def test_invalid_or_reserved_field_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "reserved"):
            Bundle(sample=Signal())
        with self.assertRaisesRegex(ValueError, "invalid"):
            Bundle({"not-valid": Signal()})

    def test_bind_and_view_as_validate_width_and_preserve_identity(self):
        class Dut:
            pass

        dut = Dut()
        dut.req_addr = Signal(width=32)
        dut.req_data = Signal(width=64)
        request = Bundle.bind(
            dut,
            {
                "addr": Field("req_addr", width=32),
                "payload": {"data": Field("req_data", width=64)},
            },
            path="request",
        )
        view = request.view_as(
            {"address": Field("addr", width=32)},
            path="request_view",
        )
        self.assertIs(request.addr, dut.req_addr)
        self.assertIs(request.payload.data, dut.req_data)
        self.assertIs(view.address, dut.req_addr)
        with self.assertRaisesRegex(ValueError, "width mismatch"):
            Bundle.bind(dut, {"addr": Field("req_addr", width=16)})

    def test_bind_tree_uses_metadata_and_maps_keywords_and_sequences(self):
        class Dut:
            pass

        dut = Dut()
        dut.io_in_bits_data = Signal(width=8)
        dut.io_in_lanes_0 = Signal(width=4)
        dut.io_in_lanes_1 = Signal(width=4)
        dut.io__debug_flag = Signal(width=1)
        tree = {
            "in": {
                "bits": {
                    "data": {"_": True, "High": 7, "Low": 0},
                },
                "lanes": {
                    "0": {"_": True, "High": 3, "Low": 0},
                    "1": {"_": True, "High": 3, "Low": 0},
                },
            }
        }
        io = Bundle.bind_tree(dut, tree, source_prefix="io", path="io")
        self.assertIs(io.in_.bits.data, dut.io_in_bits_data)
        self.assertIs(io.in_.lanes[1], dut.io_in_lanes_1)

        dut.io_in_bits_data.width = 16
        with self.assertRaisesRegex(ValueError, "metadata=8"):
            Bundle.bind_tree(dut, tree, source_prefix="io", path="io")

        debug = Bundle.bind_tree(
            dut,
            {"flag": {"_": True, "High": 0, "Low": 0}},
            source_prefix="io__debug",
            path="debug",
        )
        self.assertIs(debug.flag, dut.io__debug_flag)


class ReadyValidTests(unittest.IsolatedAsyncioTestCase):
    async def test_bind_tree_is_explicit_and_preserves_identity(self):
        dut = type("Dut", (), {})()
        dut.clk = object()
        dut.io_req_valid = Signal(0, width=1)
        dut.io_req_ready = Signal(1, width=1)
        dut.io_req_bits_addr = Signal(0x40, width=32)
        tree = {
            "valid": {"_": True, "High": 0, "Low": 0},
            "ready": {"_": True, "High": 0, "Low": 0},
            "bits": {
                "addr": {"_": True, "High": 31, "Low": 0},
            },
        }

        interface = ReadyValid.bind_tree(
            dut,
            tree,
            clock=dut.clk,
            source_prefix="io_req",
            role=Role.MONITOR,
            name="dut.req",
        )

        self.assertIs(interface.valid, dut.io_req_valid)
        self.assertIs(interface.ready, dut.io_req_ready)
        self.assertIs(interface.bits.addr, dut.io_req_bits_addr)

        malformed = dict(tree, extra={"_": True, "High": 0, "Low": 0})
        dut.io_req_extra = Signal(0, width=1)
        with self.assertRaisesRegex(ValueError, "exactly valid/ready/bits"):
            ReadyValid.bind_tree(
                dut,
                malformed,
                clock=dut.clk,
                source_prefix="io_req",
            )

    async def test_protocol_components_implement_common_contracts(self):
        clock = object()
        producer = ReadyValid(
            clock, Signal(width=1), Signal(1, width=1),
            Bundle(data=Signal()), role=Role.PRODUCER,
        )
        passive = producer.monitor_view()
        self.assertIsInstance(BundleInput(producer), Driver)
        self.assertIsInstance(ReadyValidMonitor(passive), Monitor)

    async def test_role_direction_validation_and_flipped_view(self):
        clock = object()
        valid = DirectionalSignal("in", width=1)
        ready = DirectionalSignal("out", width=1)
        data = DirectionalSignal("in")
        interface = ReadyValid(
            clock,
            valid,
            ready,
            Bundle(data=data),
            role=Role.PRODUCER,
        )
        flipped = interface.flipped()
        self.assertIs(flipped.role, Role.CONSUMER)
        self.assertEqual(valid.direction, "in")
        self.assertEqual(ready.direction, "out")
        with self.assertRaisesRegex(ValueError, "DUT output"):
            ReadyValid(
                clock,
                ready,
                valid,
                Bundle(data=data),
                role=Role.PRODUCER,
            )

    async def test_driver_serializes_and_drives_bundle(self):
        clock = object()
        valid = Signal(width=1, name="valid")
        ready = Signal(1, width=1, name="ready")
        data = Signal(width=16, name="data")
        interface = ReadyValid(
            clock, valid, ready, Bundle(data=data),
            role=Role.PRODUCER, name="req",
        )
        observed = []

        def on_phase(phase, tick):
            if phase is XPhase.RISING_STABLE and valid.value and ready.value:
                observed.append((tick, data.value))

        backend = MemoryBackend(clock, on_phase=on_phase)
        async with Execution(backend):
            driver = BundleInput(interface)
            first, second = await asyncio.gather(
                driver.send({"data": 0x11}),
                driver.send({"data": 0x22}),
            )
            driver.close()
        self.assertEqual(observed, [(2, 0x11), (4, 0x22)])
        self.assertEqual((first.tick, second.tick), (2, 4))
        self.assertEqual(valid.value, 0)

    async def test_two_drivers_cannot_own_same_signals(self):
        clock = object()
        interface = ReadyValid(
            clock,
            Signal(width=1, name="valid"),
            Signal(1, width=1, name="ready"),
            Bundle(data=Signal(name="data")),
            role=Role.PRODUCER,
        )
        backend = MemoryBackend(clock)
        async with Execution(backend):
            first = BundleInput(interface)
            second = BundleInput(interface)
            await first.__aenter__()
            with self.assertRaisesRegex(RuntimeError, "already have a driver"):
                await second.__aenter__()
            first.close()

    async def test_driver_ownership_uses_native_not_proxy_identity(self):
        clock = object()
        first_interface = ReadyValid(
            clock,
            NativeProxySignal(0x1000, width=1, name="valid-a"),
            Signal(1, width=1, name="ready-a"),
            Bundle(data=NativeProxySignal(0x2000, name="data-a")),
            role=Role.PRODUCER,
        )
        second_interface = ReadyValid(
            clock,
            NativeProxySignal(0x1000, width=1, name="valid-b"),
            Signal(1, width=1, name="ready-b"),
            Bundle(data=NativeProxySignal(0x2000, name="data-b")),
            role=Role.PRODUCER,
        )
        backend = MemoryBackend(clock)
        async with Execution(backend):
            first = BundleInput(first_interface)
            second = BundleInput(second_interface)
            await first.__aenter__()
            with self.assertRaisesRegex(RuntimeError, "already have a driver"):
                await second.__aenter__()
            first.close()

    async def test_monitor_captures_back_to_back_transfers(self):
        clock = object()
        valid = Signal(1, width=1)
        ready = Signal(1, width=1)
        data = Signal(0, width=16)
        interface = ReadyValid(
            clock, valid, ready, Bundle(data=data),
            role=Role.MONITOR, name="resp",
        )

        def on_phase(phase, tick):
            if phase is XPhase.FALLING_STABLE:
                data.value = {1: 0x10, 3: 0x20, 5: 0x30}.get(tick, data.value)
            elif phase is XPhase.RISING_STABLE:
                data.value = 0xDEAD

        backend = MemoryBackend(clock, on_phase=on_phase)
        async with Execution(backend) as execution:
            monitor = ReadyValidMonitor(interface, capacity=8).start(execution)
            transfers = [await monitor.recv() for _ in range(3)]
            await monitor.aclose()
        self.assertEqual([item.accepted_tick for item in transfers], [2, 4, 6])
        self.assertEqual(
            [item.value.data.as_int() for item in transfers],
            [0x10, 0x20, 0x30],
        )

    async def test_monitor_decoder_receives_snapshot(self):
        clock = object()
        interface = ReadyValid(
            clock, Signal(1, width=1), Signal(1, width=1),
            Bundle(data=Signal(9)), role=Role.MONITOR,
        )
        backend = MemoryBackend(clock)
        async with Execution(backend) as execution:
            monitor = ReadyValidMonitor(
                interface, decoder=lambda value: value.data.as_int(),
            ).start(execution)
            transfer = await monitor.recv()
            await monitor.aclose()
        self.assertEqual(transfer.value, 9)
        self.assertEqual(transfer.accepted_tick, 2)

    async def test_monitor_observes_python_driver_after_falling_write(self):
        clock = object()
        valid = Signal(width=1)
        ready = Signal(1, width=1)
        data = Signal(width=16)
        producer = ReadyValid(
            clock,
            valid,
            ready,
            Bundle(data=data),
            role=Role.PRODUCER,
            name="loopback",
        )
        backend = MemoryBackend(clock)
        async with Execution(backend) as execution:
            monitor = ReadyValidMonitor(
                producer.monitor_view()
            ).start(execution)
            driver = BundleInput(producer)
            accepted = await driver.send({"data": 0xCAFE})
            transfer = await monitor.recv()
            driver.close()
            await monitor.aclose()
        self.assertEqual(transfer.value.data.as_int(), 0xCAFE)
        self.assertEqual(transfer.accepted_tick, accepted.tick)

    async def test_fire_is_a_compiled_trigger(self):
        clock = object()
        valid = Signal(width=1)
        ready = Signal(width=1)
        interface = ReadyValid(
            clock, valid, ready, Bundle(data=Signal()), role=Role.MONITOR,
        )
        backend = MemoryBackend(clock)
        async with Execution(backend):
            task = asyncio.create_task(interface.fire._wait())
            await ClockCycles(clock, 1)
            self.assertFalse(task.done())
            valid.value = ready.value = 1
            event = await task
        self.assertEqual(event.phase, XPhase.DRIVE_STABLE)


if __name__ == "__main__":
    unittest.main()
