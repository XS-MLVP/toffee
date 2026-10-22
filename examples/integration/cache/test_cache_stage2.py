"""Module cases for lookup masks and forwarding not reachable in serial traffic."""
from __future__ import annotations

import pytest
from toffee import Execution, FallingEdge, RisingEdge, XCommClockBackend, as_xdata
from examples.integration.cache.cache_internal_coverage import CacheStage2Observer
from examples.integration.cache.cache_module_env import load_module
from examples.integration.cache.cache_assertion_env import assertion_run, record_assertion_checks


def load_stage2():
    return load_module("CacheStage2")


@pytest.mark.asyncio
async def test_stage2_lookup_and_forwarding(tmp_path):
    dut = load_stage2()(coverage_filename=str(tmp_path / "verilator-coverage.dat"))
    dut.InitClock("clock")
    for signal in vars(dut).values():
        data = as_xdata(signal)
        if hasattr(data, "IsInIO") and data.IsInIO():
            data.AsImmWrite()
            data.Set(0)
    observer = CacheStage2Observer(dut)
    backend = XCommClockBackend(dut.GetXClock())

    def drive(**values):
        for name, value in values.items():
            getattr(dut, name).Set(value)

    def tags(valid_mask, tag=0):
        for way in range(4):
            drive(**{f"io_metaReadResp_{way}_tag": tag if valid_mask & (1 << way) else 0,
                     f"io_metaReadResp_{way}_valid": (valid_mask >> way) & 1,
                     f"io_metaReadResp_{way}_dirty": 0})

    async def cycle():
        dut.RefreshComb()
        await RisingEdge(dut.clock)
        await FallingEdge(dut.clock)

    try:
        async with Execution(backend) as execution:
            observer.start(execution)
            try:
                await FallingEdge(dut.clock)
                drive(reset=1)
                await cycle()
                drive(reset=0, io_in_valid=1, io_in_bits_req_addr=0, io_out_ready=1)
                # All fifteen nonzero invalid-way masks, including masks that
                # the highest-invalid fill policy never produces from reset.
                for invalid in range(1, 16):
                    tags(15 ^ invalid, tag=1)
                    await cycle()
                # Match each way, then prove the same residual tag cannot hit
                # when its valid bit is cleared.
                for way in range(4):
                    tags(1 << way, tag=0)
                    await cycle()
                    tags(0)
                    await cycle()
                tags(15, tag=1)
                for _ in range(80):
                    await cycle()
                drive(io_in_valid=0)
                await cycle()
                tags(1, tag=0)
                await cycle()
                # Controlled zero-state recovery; normal seed evolution never
                # reaches zero. This is a module fault-injection obligation.
                lfsr = observer.signals["s2.victimWaymask_lfsr"]
                lfsr.AsImmWrite()
                lfsr.Set(0)
                assert int(lfsr.value) == 0
                await cycle()
                for address in (0x2fffffff, 0x30000000, 0x3fffffff, 0x40000000, 0x7fffffff, 0x80000000):
                    drive(io_in_valid=1, io_in_bits_req_addr=address)
                    tags(0)
                    await cycle()
                drive(io_in_bits_req_addr=0x2000, io_out_ready=0)
                tags(0)
                for way in range(4):
                    drive(io_metaWriteBus_req_valid=1, io_metaWriteBus_req_bits_setIdx=0,
                          io_metaWriteBus_req_bits_data_tag=1, io_metaWriteBus_req_bits_data_dirty=1,
                          io_metaWriteBus_req_bits_waymask=1 << way)
                    await cycle()
                    drive(io_metaWriteBus_req_bits_data_tag=2, io_metaWriteBus_req_bits_data_dirty=0)
                    await cycle()
                    drive(io_metaWriteBus_req_valid=0)
                    await cycle()
                    await cycle()
                for index in (1, 3):
                    drive(io_metaWriteBus_req_valid=1, io_metaWriteBus_req_bits_setIdx=index)
                    await cycle()
                drive(io_metaWriteBus_req_valid=0)
                for index in (0, 1, 8):
                    drive(io_dataWriteBus_req_valid=1, io_dataWriteBus_req_bits_setIdx=index,
                          io_dataWriteBus_req_bits_data_data=0x1122334455667788,
                          io_dataWriteBus_req_bits_waymask=1)
                    await cycle()
                drive(io_dataWriteBus_req_bits_setIdx=0, io_dataWriteBus_req_bits_data_data=0x8877665544332211)
                await cycle()
                drive(io_dataWriteBus_req_bits_data_data=0xfedcba9876543210)
                await cycle()
                drive(io_dataWriteBus_req_bits_waymask=2)
                await cycle()
                drive(io_dataWriteBus_req_valid=0)
                await cycle()
                drive(io_out_ready=1)
                await cycle()
                drive(io_in_valid=0)
                await cycle()
                drive(reset=1)
                await cycle()
            finally:
                await observer.aclose()
    finally:
        backend.close()
        observer.checks.write(tmp_path)
        dut.Finish()
    missing = {name: list(group.uncovered()) for name, group in observer.checks.groups.items()
               if group.uncovered() and name != "CACHE-INT-LOOKUP-ONEHOT"}
    assert not missing, f"uncovered Stage2 scenarios: {missing}"


def test_duplicate_tag_triggers_rtl_assertion(tmp_path, fatal_coverage_library):
    # The generated simulator aborts on $fatal. Keep this negative in its own
    # process and require the specific assertion, rather than any nonzero exit.
    code = r'''
import resource
import json
import sys
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
from test_cache_stage2 import load_stage2
from toffee import as_xdata
from examples.integration.cache.cache_assertion_env import install_fatal_coverage
dut = load_stage2()()
dut.InitClock("clock")
for signal in vars(dut).values():
    data = as_xdata(signal)
    if hasattr(data, "IsInIO") and data.IsInIO():
        data.AsImmWrite()
        data.Set(0)
dut.reset.Set(1)
dut.Step(2)
dut.reset.Set(0)
install_fatal_coverage(dut, sys.argv[1], sys.argv[2])
dut.io_in_valid.Set(1)
dut.io_in_bits_req_addr.Set(0)
for way in (0, 1):
    getattr(dut, f"io_metaReadResp_{way}_tag").Set(0)
    getattr(dut, f"io_metaReadResp_{way}_valid").Set(1)
dut.RefreshComb()
print("observed_waymask", int(dut.io_out_bits_waymask.value), flush=True)
waymask = int(dut.io_out_bits_waymask.value)
print("witness " + json.dumps({"tick": int(dut.GetXClock().GetHalfTick()), "waymask": waymask,
                             "violation": waymask.bit_count() > 1}), flush=True)
dut.Step(2)
'''
    evidence = assertion_run(code, [], library=fatal_coverage_library, output=tmp_path,
                             message="waymask", line=262, hierarchy="TOP.CacheStage2_top.CacheStage2")
    (tmp_path / "duplicate-tag-assertion.log").write_text((tmp_path / "rtl-assertion.log").read_text())
    record_assertion_checks(evidence, "stage2", "duplicate_tag", tmp_path,
                            existing=("CACHE-INT-LOOKUP-ONEHOT", "duplicate_tag_negative"))
