"""Assertion negatives run in isolated native simulator processes."""
import pytest

from examples.integration.cache.cache_assertion_env import assertion_run, record_assertion_checks


@pytest.mark.parametrize("fault, message", [
    ("write_conflict", "dataHitWriteBus.req.valid && dataRefillWriteBus.req.valid"),
    ("flush", "only allow to flush icache"),
    ("mmio_hit", "MMIO request should not hit in cache"),
    ("meta_conflict", "metaHitWriteBus.req.valid && metaRefillWriteBus.req.valid"),
])
def test_stage3_rtl_assertions(tmp_path, fatal_coverage_library, fault, message):
    script = r'''
import json, resource, sys
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
from examples.integration.cache.cache_module_env import initialize_module, load_module
from examples.integration.cache.cache_assertion_env import install_fatal_coverage
dut = load_module("CacheStage3")()
dut.InitClock("clock")
initialize_module(dut)
dut.reset.Set(1)
dut.Step(2)
dut.reset.Set(0)
install_fatal_coverage(dut, sys.argv[2], sys.argv[3])
fault = sys.argv[1]
if fault in ("write_conflict", "meta_conflict"):
    state = dut.GetInternalSignal("CacheStage3_top.CacheStage3.state")
    state.AsImmWrite()
    state.Set(2)
    dut.io_in_valid.Set(1)
    dut.io_in_bits_hit.Set(1)
    dut.io_in_bits_req_cmd.Set(1)
    dut.io_in_bits_waymask.Set(1)
    dut.io_in_bits_metas_0_dirty.Set(int(fault == "write_conflict"))
    dut.io_mem_resp_valid.Set(1)
    dut.io_mem_resp_bits_cmd.Set(6 if fault == "meta_conflict" else 4)
elif fault == "mmio_hit":
    dut.io_in_valid.Set(1)
    dut.io_in_bits_hit.Set(1)
    dut.io_in_bits_mmio.Set(1)
    dut.io_in_bits_waymask.Set(1)
else:
    dut.io_flush.Set(1)
dut.RefreshComb()
prefix = "CacheStage3_top.CacheStage3."
if fault == "write_conflict":
    names = ("hitWrite", "dataRefillWriteBus_x9")
elif fault == "meta_conflict":
    names = ("metaHitWriteBus_x5", "metaRefillWriteBus_req_valid")
elif fault == "mmio_hit":
    names = ("mmio", "hit")
else:
    names = ("io_flush",)
condition = {name: int(dut.GetInternalSignal(prefix + name).value) for name in names}
violation = all(condition.values())
print("witness " + json.dumps({"tick": int(dut.GetXClock().GetHalfTick()), "violation": violation}), flush=True)
dut.Step(2)
'''
    line = {"write_conflict": 924, "flush": 948, "mmio_hit": 876, "meta_conflict": 900}[fault]
    evidence = assertion_run(script, [fault], library=fatal_coverage_library, output=tmp_path,
                             message=message, line=line, hierarchy="TOP.CacheStage3_top.CacheStage3")
    existing = ("CACHE-INT-ARBITRATION-WRITE-EXCLUSION", "negative_conflict") if fault == "write_conflict" else None
    record_assertion_checks(evidence, "stage3", fault, tmp_path, existing=existing)


@pytest.mark.parametrize("fault, message, line", [
    ("duplicate_tag", "waymask", 262),
    ("mmio_hit", "MMIO request should not hit in cache", 876),
    ("meta_conflict", "metaHitWriteBus.req.valid && metaRefillWriteBus.req.valid", 900),
    ("write_conflict", "dataHitWriteBus.req.valid && dataRefillWriteBus.req.valid", 924),
    ("flush", "only allow to flush icache", 948),
])
def test_full_cache_rtl_assertions(tmp_path, fatal_coverage_library, fault, message, line):
    script = r'''
import json, resource, sys
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
import cache_functional_toffee as cache
from examples.integration.cache.cache_assertion_env import install_fatal_coverage
dut = cache.DUTCacheSignalCFG()
cache.prepare_dut(dut)
dut.reset.Set(1)
dut.Step(2)
dut.reset.Set(0)
dut.Step(132)
assert int(dut.io_in_req_ready.value) == 1
install_fatal_coverage(dut, sys.argv[2], sys.argv[3])
fault = sys.argv[1]
prefix = "CacheSignalCFG_top.Cache."
injected = {}
def inject(name, value):
    signal = dut.GetInternalSignal(prefix + name)
    signal.AsImmWrite()
    signal.Set(value)
    assert int(signal.value) == value
    injected[name] = value
if fault == "duplicate_tag":
    # Corrupt real RAM cells, then use the public CPU request interface.
    for way in (0, 1):
        inject(f"metaArray.ram.array_{way}", 2)  # tag=0, valid=1, dirty=0, set=0
    dut.io_in_req_bits_addr.Set(0)
    dut.io_in_req_bits_cmd.Set(0)
    dut.io_in_req_bits_size.Set(3)
    dut.io_in_req_valid.Set(1)
    dut.Step(1)
    dut.io_in_req_valid.Set(0)
elif fault == "flush":
    dut.io_flush.Set(2)
else:
    inject("valid_1", 1)
    for field, value in {"hit": 1, "mmio": int(fault == "mmio_hit"), "waymask": 1,
                         "req_addr": 0x40000000 if fault == "mmio_hit" else 0x2040,
                         "req_cmd": 0 if fault == "mmio_hit" else 1,
                         "req_wmask": 0xff, "metas_0_dirty": int(fault == "write_conflict")}.items():
        inject("s3_io_in_bits_r_" + field, value)
    if fault in ("meta_conflict", "write_conflict"):
        inject("s3.state", 2)
        dut.io_out_mem_resp_valid.Set(1)
        dut.io_out_mem_resp_bits_cmd.Set(6 if fault == "meta_conflict" else 4)
dut.RefreshComb()
if fault == "duplicate_tag":
    condition = {"waymask": int(dut.GetInternalSignal(prefix + "s2.waymask").value)}
    violation = condition["waymask"].bit_count() > 1
else:
    names = {"mmio_hit": ("mmio", "hit"), "meta_conflict": ("metaHitWriteBus_x5", "metaRefillWriteBus_req_valid"),
             "write_conflict": ("hitWrite", "dataRefillWriteBus_x9"), "flush": ("io_flush",)}[fault]
    condition = {name: int(dut.GetInternalSignal(prefix + "s3." + name).value) for name in names}
    violation = all(condition.values())
print("witness " + json.dumps({"tick": int(dut.GetXClock().GetHalfTick()), "violation": violation,
                             "condition": condition, "injected_registers": injected}), flush=True)
dut.Step(1)
'''
    hierarchy = "TOP.CacheSignalCFG_top.Cache." + ("s2" if fault == "duplicate_tag" else "s3")
    evidence = assertion_run(script, [fault], library=fatal_coverage_library, output=tmp_path,
                             message=message, line=line, hierarchy=hierarchy)
    record_assertion_checks(evidence, "cache", fault, tmp_path)
