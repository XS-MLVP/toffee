"""Run against a Picker-generated UnpackedSignals package; see README.md."""

import argparse
import asyncio
import json
from pathlib import Path

from UnpackedSignals import DUTUnpackedSignals, xsp
from toffee import (
    Bundle, Execution, ReadyValid, ReadyValidMonitor, RisingEdge, Role, Scoreboard,
    SignalArray, SyncDriver, XCommClockBackend, drive_ready_valid, xtrigger,
)


@xtrigger(sample=RisingEdge("clk"))
def selected_result(dut, expected):
    return dut.result[3][-1] == expected


async def verify(metadata, access):
    dut = DUTUnpackedSignals()
    backend = None
    try:
        dut.InitClock("clk")
        pins = Bundle.bind_tree(dut, json.loads(Path(metadata).read_text()))
        assert pins.matrix.bounds == ((3, 2), (-1, 1))
        assert pins.result.bounds == ((2, 3), (1, -1))
        assert pins.cube.shape == (2, 2, 3)
        assert pins.matrix[3][-1].CSelf() == dut["matrix[3][-1]"].CSelf()
        kind = xsp.XDataBackendKind_DPI if access == "dpi" else xsp.XDataBackendKind_MemDirect
        assert pins.matrix[3][-1].GetBackendKind() == kind

        # Leave input arrays in their generated Rise write mode. XPort must
        # continue committing them, even though Toffee sees bounded views.
        pins.matrix.drive([[0, 0, 0], [0, 0, 0]])
        cube_values = {i: {j: {k: i * 32 + j * 8 + k for k in (4, 5, 6)}
                           for j in (-1, 0)} for i in (3, 2)}
        pins.cube.drive(cube_values)
        dut.valid.Set(0)
        dut.Step(1)
        assert pins.tensor.sample().as_dict() == {
            i: {j: {k: cube_values[i][j][k] ^ (i * 16 + j * 4 + k)
                    for k in (6, 5, 4)} for j in (0, -1)} for i in (2, 3)
        }

        producer = ReadyValid(dut.clk, dut.valid, dut.ready,
                              Bundle(matrix=pins.matrix), role=Role.PRODUCER)

        class Input(SyncDriver[dict]):
            async def _drive_one(self, request):
                return await drive_ready_valid(dut.clk, dut.valid, dut.ready,
                                               lambda: producer.bits.drive(request), bits=producer.bits)

        backend = XCommClockBackend(dut)
        scoreboard = Scoreboard("unpacked_results")
        async with asyncio.timeout(20):
            async with Execution(backend) as execution:
                monitor = ReadyValidMonitor(producer.monitor_view()).start(execution)
                driver = Input((dut.valid, *(leaf for _, leaf in producer.bits.leaves())), name="matrix_input")
                try:
                    for transaction in range(4):
                        values = {i: {j: (1 << 64) | (transaction * 256 + i * 16 + j + 1)
                                      for j in (-1, 0, 1)} for i in (3, 2)}
                        accepted = await driver.send({"matrix": values})
                        observed = await monitor.recv()
                        assert observed.accepted_tick == accepted.tick
                        assert observed.value.as_dict() == {"matrix": values}
                        expected = {i: {j: values[i][j] ^ (i * 16 + j + 1)
                                        for j in (1, 0, -1)} for i in (2, 3)}
                        scoreboard.check(expected=expected, actual=pins.result.sample().as_dict())
                        await selected_result(dut, expected[3][-1])
                finally:
                    driver.close()
                    await monitor.aclose()
        backend.close()
        backend = None

        if access == "mem_direct":
            name = next(name for name in dut.GetInternalSignalList() if name.endswith(".memory"))
            native = dut.xcfg.NewXDataArrayView(name)
            memory = SignalArray(native)
            assert memory.bounds == ((0, 1), (0, 2))
            assert tuple(native.Strides()) == (36, 12)
            old = dut.GetInternalSignal(name, is_array=True)
            assert len(old) == 6
            for _, leaf in memory.leaves():
                leaf.AsImmWrite()
            memory.drive([[1 << 64, 2, 3], [4, 5, 99]])
            assert old[0].U() == 1 << 64 and old[5].U() == 99
            assert memory[0][0].CSelf() == native.At([0, 0]).CSelf()
            dut.RefreshComb()
            assert dut.probe.U() == (1 << 64) ^ 99
        assert scoreboard.status.checked == 4 and scoreboard.status.failed == 0
        print(f"UnpackedSignals {access}: ports, metadata, driver/monitor, static trigger and snapshots PASS")
    finally:
        if backend is not None:
            backend.close()
        dut.Finish()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--access", choices=("dpi", "mem_direct"), required=True)
    args = parser.parse_args()
    asyncio.run(verify(args.metadata, args.access))
