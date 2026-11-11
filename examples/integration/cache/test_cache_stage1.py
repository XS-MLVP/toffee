"""Combinational Stage1 readiness and address decomposition truth tables."""
from itertools import product
import os

import pytest
from toffee import CombStable, Execution, XCommClockBackend

from examples.integration.rtl_checks import ScenarioChecks
from examples.integration.cache.cache_internal_coverage import RULES
from examples.integration.cache.cache_module_env import initialize_module, load_module, ModuleCycles


@pytest.mark.asyncio
async def test_stage1_joint_readiness_and_indices(tmp_path):
    dut = load_module("CacheStage1")(coverage_filename=str(tmp_path / "verilator-coverage.dat"))
    initialize_module(dut)
    backend = XCommClockBackend(dut)
    drive = ModuleCycles(dut).drive
    rules = {key: RULES[key] for key in ("CACHE-INT-PIPE-S1-JOINT-READY", "CACHE-INT-PIPE-S1-INDEX")}
    checks = ScenarioChecks(rules, run_metadata={"test": os.environ.get("PYTEST_CURRENT_TEST"), "module": "CacheStage1"},
                            contract="cache.rtl-properties/v1")
    checks.implemented = set(rules)
    try:
        async with Execution(backend):
            for valid, downstream, meta, data in product((0, 1), repeat=4):
                drive(io_in_valid=valid, io_out_ready=downstream,
                      io_metaReadBus_req_ready=meta, io_dataReadBus_req_ready=data)
                event = await CombStable()
                expected = (int(bool(valid and meta and data)),
                            int(bool(meta and data and (not valid or downstream))),
                            int(bool(valid and downstream)), int(bool(valid and downstream)))
                actual = tuple(int(getattr(dut, name).value) for name in
                               ("io_out_valid", "io_in_ready", "io_metaReadBus_req_valid", "io_dataReadBus_req_valid"))
                checks.check("CACHE-INT-PIPE-S1-JOINT-READY",
                             "meta_blocked" if not meta else "data_blocked" if not data else "both_ready",
                             actual=actual, expected=expected, tick=event.tick,
                             context={"observation_kind": "settled_combinational", "delta": event.delta,
                                      "valid": valid, "downstream_ready": downstream,
                                      "meta_ready": meta, "data_ready": data})
            for addr, scenario in ((0x38, "word_boundary"), (0x40, "set_boundary"), (0x1ff8, "tag_boundary"), (0x2000, "tag_boundary")):
                drive(io_in_bits_addr=addr)
                event = await CombStable()
                checks.check("CACHE-INT-PIPE-S1-INDEX", scenario,
                             actual=(int(dut.io_metaReadBus_req_bits_setIdx.value), int(dut.io_dataReadBus_req_bits_setIdx.value)),
                             expected=((addr >> 6) & 127, (addr >> 3) & 1023), tick=event.tick,
                             context={"observation_kind": "settled_combinational", "delta": event.delta,
                                      "address": addr})
    finally:
        backend.close()
        checks.write(tmp_path)
        dut.Finish()
    assert not checks.failures
    for group in checks.groups.values():
        group.assert_coverage(100)
