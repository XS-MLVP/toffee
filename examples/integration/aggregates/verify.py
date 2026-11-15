"""Exercise real Picker aggregates through Toffee's native execution paths."""

import argparse
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from AggregateSignals import DUTAggregateSignals, xsp
from toffee import (
    Bin, Bundle, CoverGroupDef, CoverPointDef, Execution, ReadyValid,
    ReadyValidMonitor, RisingEdge, Role, Scoreboard, SyncDriver,
    XCommClockBackend, drive_ready_valid, xtrigger,
)


@xtrigger(sample=RisingEdge("clk"))
def selected_result(dut, expected):
    return dut.record_out.entries[-1].header.tag == expected


def transaction(number):
    return {
        "record": {"enabled": number & 1, "entries": {
            -1: {"header": {"tag": 17 + number, "valid": 1},
                 "payload": {3: (1 << 64) | 7, 2: number * 11}},
            0: {"header": {"tag": 3, "valid": 0},
                "payload": {3: (1 << 64) | number, 2: number + 9}},
        }},
        "header": {"tag": number + 5, "valid": 1},
        "matrix": {1: {-2: 1, -1: number & 1, 0: 0, 1: 1},
                   0: {-2: 0, -1: 1, 0: number & 1, 1: 0}},
    }


async def verify(metadata, access):
    dut = DUTAggregateSignals()
    backend = None
    try:
        dut.InitClock("clk")
        # Run the model's initialization before querying/driving VPI values;
        # Verilator's first evaluation can initialize and overwrite those nets.
        dut.Step(1)
        clock = dut.GetXClock()
        port_count = dut.xport.PortCount()
        tree = json.loads(Path(metadata).read_text() if metadata else dut.xcfg.Metadata("ports"))
        pins = Bundle.bind_tree(dut, tree)
        # Picker's signal tree groups common name prefixes; `in` is exposed as
        # `in_` by the existing Bundle.bind_tree keyword rule.
        assert pins.record.in_.entries.bounds == ((-1, 0),)
        assert pins.record.in_.entries[-1].payload.bounds == ((3, 2),)
        assert pins.matrix.in_.bounds == ((1, 0), (-2, 1))
        assert pins.packed.in_.tag.CSelf() != dut.packed_in.CSelf()
        assert pins.record.in_.entries[-1].payload[3].CSelf() == dut["record_in.entries[-1].payload[3]"].CSelf()
        expected_kind = xsp.XDataBackendKind_DPI if access != "mem_direct" else xsp.XDataBackendKind_MemDirect
        assert pins.record.in_.entries[-1].payload[3].GetBackendKind() == expected_kind
        assert dut.xport.PortCount() == port_count
        if access == "vpi":
            sources = {name: dut.GetInternalSignal(name, use_vpi=True) for name in (
                "record_in", "record_out", "packed_in", "packed_out", "matrix_in", "matrix_out",
            )}
            inputs = Bundle(record=sources["record_in"], header=sources["packed_in"], matrix=sources["matrix_in"])
            outputs = Bundle(record=sources["record_out"], header=sources["packed_out"], matrix=sources["matrix_out"])
            # Queried VPI leaves are not registered with the generated XPort.
            # Choose immediate writes explicitly; adaptation itself never changes modes.
            for _, leaf in inputs.leaves():
                leaf.AsImmWrite()
            trigger_root = SimpleNamespace(record_out=sources["record_out"], clk=clock)
        else:
            inputs = Bundle(record=pins.record.in_, header=pins.packed.in_, matrix=pins.matrix.in_)
            outputs = Bundle(record=pins.record.out, header=pins.packed.out, matrix=pins.matrix.out)
            trigger_root = SimpleNamespace(record_out=dut.record_out, clk=clock)
        sampler = outputs.compile_sampler()
        interface = ReadyValid(clock, dut.valid, dut.ready, inputs, role=Role.PRODUCER)

        class Input(SyncDriver[dict]):
            async def _drive_one(self, request):
                return await drive_ready_valid(clock, dut.valid, dut.ready,
                    lambda: interface.bits.drive(request), bits=interface.bits)

        coverage = CoverGroupDef("aggregate_payload", (
            CoverPointDef("word", {"wide": Bin.values((1 << 64) | 7)}),
        )).instantiate("dut")
        coverage.bind(trigger=RisingEdge(clock),
                      fields={"word": outputs.record.entries[-1].payload[3]}, strategy="native")
        backend = XCommClockBackend(dut)
        scoreboard = Scoreboard("aggregate_results")
        async with asyncio.timeout(20):
            async with Execution(backend, coverage=[coverage]) as execution:
                monitor = ReadyValidMonitor(interface.monitor_view()).start(execution)
                driver = Input((dut.valid, *(leaf for _, leaf in inputs.leaves())), name="aggregate_input")
                saved = []
                try:
                    for number in range(4):
                        expected = transaction(number)
                        accepted = await driver.send(expected)
                        observed = await monitor.recv()
                        assert observed.accepted_tick == accepted.tick
                        assert observed.value.as_dict() == expected
                        scoreboard.check(expected=expected, actual=sampler().as_dict())
                        await selected_result(trigger_root, 17 + number)
                        saved.append(observed.value)
                finally:
                    driver.close()
                    await monitor.aclose()
        assert saved[0].as_dict() == transaction(0)
        assert coverage.report()["sampling_backend"] == "native"
        coverage.assert_coverage()
        assert scoreboard.status.checked == 4 and scoreboard.status.failed == 0
        assert dut.xport.PortCount() == port_count
        print(f"AggregateSignals {access}: metadata, driver/monitor, snapshots, native trigger/coverage PASS")
    finally:
        if backend is not None:
            backend.close()
        dut.Finish()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--metadata", type=Path, help="explicit JSON override; defaults to the DUT configuration database")
    parser.add_argument("--access", choices=("dpi", "mem_direct", "vpi"), required=True)
    args = parser.parse_args()
    asyncio.run(verify(args.metadata, args.access))
