"""Stage3 state/beat cases with independent SRAM input words and bus events."""
import json
import pytest

from toffee import Execution, FallingEdge, XCommClockBackend
from examples.integration.cache.cache_internal_coverage import CacheStage3Observer
from examples.integration.cache.cache_module_env import initialize_module, load_module, ModuleCycles


class Stage3Cases(ModuleCycles):
    def __init__(self, dut, observer):
        super().__init__(dut)
        self.observer = observer

    def begin(self, addr, cmd=0, *, hit=False, mmio=False, dirty=False, way=0, mask=0xff):
        index, word = (addr >> 6) & 127, (addr >> 3) & 7
        self.drive(io_in_valid=1, io_in_bits_req_addr=addr, io_in_bits_req_cmd=cmd,
                   io_in_bits_hit=int(hit), io_in_bits_mmio=int(mmio), io_in_bits_waymask=1 << way,
                   io_in_bits_req_wmask=mask, io_in_bits_req_wdata=0xfedcba9876543210,
                   io_in_bits_isForwardData=0, io_out_ready=0, io_mem_req_ready=0, io_mem_resp_valid=0,
                   io_mmio_req_ready=0, io_mmio_resp_valid=0, io_cohResp_ready=0,
                   io_dataReadBus_req_ready=0)
        for w in range(4):
            victim_tag = 0x19000 + w
            self.observer.directory[index, w] = (victim_tag, 1, int(dirty and w == way))
            self.drive(**{f"io_in_bits_metas_{w}_tag": victim_tag,
                          f"io_in_bits_metas_{w}_dirty": int(dirty and w == way)})
            for beat in range(8):
                key = ((index << 3) | beat, w)
                self.observer.data.setdefault(key, 0xa1b2c3d400000000 | (w << 16) | (beat << 8) | index)
            self.drive(**{f"io_in_bits_datas_{w}_data": self.observer.data[(index << 3) | word, w]})

    async def idle(self):
        assert int(self.dut.GetInternalSignal("CacheStage3_top.CacheStage3.state").value) == 0
        await self.advance(io_in_valid=0, io_mem_resp_valid=0, io_mmio_resp_valid=0, io_out_ready=0, io_cohResp_ready=0)

    async def refill(self, addr, *, early=False):
        # Includes bus cmd=last during a valid-low gap, before the real last.
        await self.advance()
        await self.advance(io_mem_req_ready=1)
        await self.advance(io_mem_resp_valid=0, io_mem_resp_bits_cmd=6)
        start = (addr >> 3) & 7
        for beat in range(8):
            value = 0x1234567800000000 | (((start + beat) & 7) << 8) | beat
            await self.advance(io_mem_resp_valid=1, io_mem_resp_bits_cmd=6 if beat == 7 else 4,
                               io_mem_resp_bits_rdata=value)
            if beat != 7:
                await self.advance(io_mem_resp_valid=0, io_mem_resp_bits_cmd=6 if beat == 6 else 4,
                                   io_out_ready=int(early))
        await self.advance(io_mem_resp_valid=0, io_out_ready=0)
        if not early:
            await self.advance(io_out_ready=1)
        await self.idle()

    async def read_beats(self, *, channel, count=8):
        state = self.observer.signals["s3.state"]
        for beat in range(count):
            # Stall the SRAM grant, capture the returned four words, then stall
            # selected output beats. These are separate acceptance events.
            await self.advance(io_dataReadBus_req_ready=0)
            await self.advance(io_dataReadBus_req_ready=1)
            index = int(self.dut.io_dataReadBus_req_bits_setIdx.value)
            # The request's word is stable throughout the read-capture substate.
            for way in range(4):
                self.drive(**{f"io_dataReadBus_resp_data_{way}_data": self.observer.data[index, way]})
            await self.advance()
            ready = "io_mem_req_ready" if channel == "memory" else "io_cohResp_ready"
            if beat in (0, 3, 7):
                await self.advance(**{ready: 0})
                await self.advance(**{ready: 0})
            await self.advance(**{ready: 1})
            self.drive(**{ready: 0})
        assert int(state.value) == (4 if channel == "memory" else 0)


@pytest.mark.asyncio
async def test_stage3_refill_writeback_mmio_and_release(tmp_path):
    dut = load_module("CacheStage3")(coverage_filename=str(tmp_path / "verilator-coverage.dat"))
    dut.InitClock("clock")
    initialize_module(dut)
    observer = CacheStage3Observer(dut)
    cases = Stage3Cases(dut, observer)
    backend = XCommClockBackend(dut.GetXClock())
    try:
        async with Execution(backend) as execution:
            observer.start(execution)
            try:
                await FallingEdge(dut.clock)
                await cases.advance(reset=1)
                await cases.advance(reset=0)
                for start in range(8):
                    addr = 0x2040 + start * 8
                    cases.begin(addr)
                    await cases.advance()
                    await cases.refill(addr, early=start % 2 == 0)
                for mask in (0, 0xa5):
                    addr = 0x30f8
                    cases.begin(addr, cmd=1, mask=mask)
                    await cases.advance()
                    await cases.refill(addr)
                for way in range(4):
                    addr = 0x4000
                    cases.begin(addr, dirty=True, way=way)
                    await cases.advance()
                    await cases.read_beats(channel="memory")
                    await cases.advance(io_mem_resp_valid=0)
                    await cases.advance()
                    await cases.advance(io_mem_resp_valid=1, io_mem_resp_bits_cmd=5)
                    cases.drive(io_mem_resp_valid=0)
                    await cases.refill(addr, early=True)
                for cmd in (0, 1):
                    cases.begin(0x40000000, cmd=cmd, mmio=True)
                    await cases.advance()
                    await cases.advance()
                    await cases.advance(io_mmio_req_ready=1)
                    await cases.advance(io_mmio_resp_valid=0)
                    await cases.advance()
                    await cases.advance(io_mmio_resp_valid=1, io_mmio_resp_bits_rdata=0xdeadbeef12345678)
                    await cases.advance(io_mmio_resp_valid=0)
                    await cases.advance(io_out_ready=1)
                    await cases.idle()
                # CPU write burst, ordinary write afterwards, and consecutive
                # partial writes without an intervening empty cycle.
                addr = 0x5040
                for beat in range(8):
                    cases.begin(addr + beat * 8, cmd=7 if beat == 7 else 3, hit=True, mask=1 << beat)
                    await cases.advance()
                    await cases.advance(io_out_ready=1)
                    await cases.idle()
                for masks in ((1, 2), (3, 1)):
                    cases.begin(addr, cmd=1, hit=True, mask=masks[0])
                    await cases.advance(io_out_ready=1)
                    cases.begin(addr, cmd=1, hit=True, dirty=True, mask=masks[1])
                    await cases.advance(io_out_ready=1)
                    await cases.idle()
                cases.begin(addr, cmd=1, hit=True, mask=0)
                await cases.advance(io_out_ready=1)
                await cases.idle()
                # Forward data is usable only for the selected way.
                for match in (True, False):
                    cases.begin(addr, hit=True)
                    value = observer.data[((addr >> 3) & 1023), 0]
                    await cases.advance(io_in_bits_isForwardData=1, io_in_bits_forwardData_waymask=1 if match else 2,
                                        io_in_bits_forwardData_data_data=value)
                    await cases.advance(io_out_ready=1)
                    await cases.idle()
                for dirty in (False, True):
                    for way in range(4):
                        for start in range(8) if way == 0 else (0,):
                            cases.begin(0x6040 + start * 8, cmd=8, hit=True, dirty=dirty, way=way)
                            await cases.advance()
                            await cases.advance(io_cohResp_ready=1)
                            cases.drive(io_cohResp_ready=0)
                            await cases.read_beats(channel="coherence")
                            await cases.idle()
                cases.begin(0x6040, cmd=8)
                await cases.advance(io_cohResp_ready=1)
                await cases.idle()
                cases.begin(0x6040, cmd=8, hit=True)
                await cases.advance(io_cohResp_ready=1)
                cases.drive(io_cohResp_ready=0)
                await cases.read_beats(channel="coherence")
                await cases.idle()
            finally:
                await observer.aclose()
    finally:
        backend.close()
        observer.checks.write(tmp_path)
        dut.Finish()
    assert not observer.checks.failures


@pytest.mark.asyncio
@pytest.mark.parametrize("start", (0, 7))
async def test_read_burst_exposes_repeated_demand_word(tmp_path, start):
    """Retain a DUT defect witness separately from passing functional evidence."""
    dut = load_module("CacheStage3")(coverage_filename=str(tmp_path / "verilator-coverage.dat"))
    dut.InitClock("clock")
    initialize_module(dut)
    observer = CacheStage3Observer(dut)
    cases = Stage3Cases(dut, observer)
    backend = XCommClockBackend(dut.GetXClock())
    try:
        with pytest.raises(AssertionError, match="CACHE-INT-BURST-READ-HIT"):
            async with Execution(backend) as execution:
                observer.start(execution)
                try:
                    await FallingEdge(dut.clock)
                    await cases.advance(reset=1)
                    await cases.advance(reset=0)
                    addr = 0x7040 + start * 8
                    cases.begin(addr, cmd=2, hit=True)
                    await cases.advance()  # initial consumer backpressure
                    await cases.advance(io_out_ready=1)  # first word accepted
                    cases.drive(io_out_ready=0)
                    await cases.advance(io_dataReadBus_req_ready=1)
                    index = int(dut.io_dataReadBus_req_bits_setIdx.value)
                    for way in range(4):
                        cases.drive(**{f"io_dataReadBus_resp_data_{way}_data": observer.data[index, way]})
                    await cases.advance()
                    await cases.advance(io_out_ready=1)
                finally:
                    await observer.aclose()
    finally:
        backend.close()
        observer.checks.write(tmp_path)
        (tmp_path / "rtl-defects.json").write_text(json.dumps({
            "defect": "CPU read-burst repeats the demand word instead of the next SRAM word",
            "source": "example/CacheSignalCFG/Cache.v", "start_word": start,
            "expected_failure": "CACHE-INT-BURST-READ-HIT", "witnesses": observer.checks.failures,
        }, indent=2) + "\n")
        dut.Finish()
    assert len(observer.checks.failures) == 1
    failure = observer.checks.failures[0]
    assert failure["context"]["observation"] == "burst_data"
    assert failure["actual"] != failure["expected"]
