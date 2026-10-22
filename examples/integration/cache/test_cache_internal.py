"""Full-Cache pipeline/concurrency cases selected from observed RTL gaps."""
import asyncio
import json
from types import SimpleNamespace

import pytest
import pytest_asyncio

import cache_functional_toffee as cache
from toffee import Execution, FallingEdge, RisingEdge, ReadyValidMonitor, Scoreboard, Value, XCommClockBackend
from examples.integration.cache.cache_internal_coverage import CacheInternalObserver


@pytest_asyncio.fixture
async def bench(tmp_path):
    dut = cache.DUTCacheSignalCFG(coverage_filename=str(tmp_path / "verilator-coverage.dat"))
    backend = None
    observer = None
    try:
        backend = XCommClockBackend(cache.prepare_dut(dut))
        interfaces = cache.bind_cache_interfaces(dut)
        coherence = cache.CoherenceAgent(interfaces)
        backing = cache.ReferenceMemory()
        memory = cache.MemoryModel(dut, backing, interfaces, response_gap=1)
        mmio = cache.MmioModel(dut, cache.ReferenceMemory(), interfaces)
        driver = cache.CacheDriver(dut, interfaces)
        observer = CacheInternalObserver(dut)
        async with Execution(backend, agents=[coherence]) as execution:
            observer.start(execution)
            tasks = []
            try:
                await cache.reset_and_wait_ready(dut)
                # Unreset registers may already drive valid before the first
                # reset edge. Responder ledgers start after hardware reset.
                tasks.extend((asyncio.create_task(memory.run()), asyncio.create_task(mmio.run())))
                yield SimpleNamespace(dut=dut, execution=execution, interfaces=interfaces,
                                      coherence=coherence, memory=memory, mmio=mmio, driver=driver,
                                      backing=backing, observer=observer, tasks=tasks, output=tmp_path)
            finally:
                driver.close()
                for task in tasks:
                    task.cancel()
                results = await asyncio.gather(*tasks, return_exceptions=True)
                await observer.aclose()
                errors = [r for r in results if isinstance(r, BaseException) and not isinstance(r, asyncio.CancelledError)]
                if errors:
                    raise BaseExceptionGroup("Cache responders failed", errors)
    finally:
        if observer is not None:
            observer.checks.write(tmp_path)
        if backend is not None:
            assert backend.watcher_count == 0 and backend._owner is None
            backend.close()
        dut.Finish()


async def idle(bench):
    await Value(bench.dut.io_empty, 1, sample=FallingEdge(bench.dut.clock))


def inject_control(bench, name, value):
    signal = bench.dut.GetInternalSignal("CacheSignalCFG_top.Cache." + name)
    signal.AsImmWrite()
    signal.Set(value)
    assert int(signal.value) == value
    return signal


@pytest.mark.asyncio
async def test_full_cache_lfsr_recovers_from_zero(bench):
    await FallingEdge(bench.dut.clock)
    lfsr = inject_control(bench, "s2.victimWaymask_lfsr", 0)
    await RisingEdge(bench.dut.clock)
    await FallingEdge(bench.dut.clock)
    assert int(lfsr.value) == 1
    assert "scenario.zero_recovery" not in bench.observer.checks.groups["CACHE-INT-LOOKUP-LFSR"].uncovered()
    addr = 0x22040
    assert await bench.driver.read(addr) == bench.backing.read(addr)
    await idle(bench)
    (bench.output / "lfsr-recovery-witness.json").write_text(json.dumps({
        "injected": 0, "next_value": 1, "recovery_address": addr,
        "rule": "explicit zero-state recovery in CacheStage2 RTL",
    }, indent=2) + "\n")


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ("main_state", "read_substate", "both"))
async def test_reset_recovers_illegal_cache_control_state(bench, fault):
    """Reset is specified; autonomous illegal-state recovery is not assumed."""
    addr = 0x24040
    assert await bench.driver.read(addr) == bench.backing.read(addr)
    await idle(bench)
    before_reads = len(bench.memory.read_bursts)
    await FallingEdge(bench.dut.clock)
    injected = {}
    if fault in ("main_state", "both"):
        inject_control(bench, "s3.state", 15)
        injected["s3.state"] = 15
    if fault in ("read_substate", "both"):
        inject_control(bench, "s3.state2", 3)
        injected["s3.state2"] = 3
    before_reset = bench.observer.snapshot()
    await RisingEdge(bench.dut.clock)
    await FallingEdge(bench.dut.clock)
    await cache.reset_and_wait_ready(bench.dut)
    after_reset = bench.observer.snapshot()
    controls = ("valid", "valid_1", "s3.state", "s3.state2", "s3.readBeatCnt_value",
                "s3.writeBeatCnt_value", "s3.writeL2BeatCnt_value", "s3.afterFirstRead", "s3.alreadyOutFire")
    actual = {name: after_reset[name] for name in controls}
    for _ in range(3):
        await FallingEdge(bench.dut.clock)
        assert cache.get(bench.dut.io_in_resp_valid) == 0
    recovery_data = await bench.driver.read(addr)
    await idle(bench)
    bench.observer.checks.check("CACHE-INT-ERROR-STATE-RESET", fault,
        actual={**actual, "recovery_data": recovery_data, "recovery_refills": bench.memory.read_bursts[before_reads:]},
        expected={**dict.fromkeys(controls, 0), "recovery_data": bench.backing.read(addr), "recovery_refills": [addr]},
        tick=int(bench.dut.GetXClock().GetHalfTick()),
        context={"injected": injected, "before_reset": before_reset})
    (bench.output / "illegal-state-reset-witness.json").write_text(json.dumps({
        "fault": fault, "injected": injected, "before_reset": before_reset,
        "after_reset": after_reset, "recovery_address": addr,
        "contract": "reset recovery; autonomous illegal-state behavior is unspecified",
    }, indent=2) + "\n")


@pytest.mark.asyncio
@pytest.mark.parametrize("finish", ("accept", "reset"))
async def test_faulted_flush_latch_clears_on_completion_or_reset(bench, finish):
    addr, user = 0x26040, 0x601
    assert await bench.driver.read(addr) == bench.backing.read(addr)
    await idle(bench)
    before_reads = len(bench.memory.read_bursts)
    monitor = ReadyValidMonitor(bench.interfaces.cpu_response.monitor_view(), capacity=4).start(bench.execution)
    try:
        async with asyncio.timeout(5):
            await bench.driver.send({"addr": addr, "size": 3, "cmd": cache.CMD_READ,
                                     "wmask": 0, "wdata": 0, "user": user})
            await FallingEdge(bench.dut.clock)
            await RisingEdge(bench.dut.clock)
            await FallingEdge(bench.dut.clock)
            cache.set_(bench.dut.io_in_resp_ready, 0)
            flag = inject_control(bench, "s3.needFlush", 1)
            held = []
            for _ in range(3):
                await RisingEdge(bench.dut.clock)
                await FallingEdge(bench.dut.clock)
                assert int(flag.value) == 1
                assert cache.get(bench.dut.io_in_resp_valid) == 1
                actual = {name: value.as_int() for name, value in bench.interfaces.cpu_response.bits.sample().items()}
                Scoreboard("cache.faulted-flush-stall").check(
                    expected={"cmd": cache.CMD_READ_LAST, "rdata": bench.backing.read(addr), "user": user}, actual=actual)
                held.append(actual)
            if finish == "accept":
                cache.set_(bench.dut.io_in_resp_ready, 1)
                response = await monitor.recv()
                Scoreboard("cache.faulted-flush-response").check(expected=held[0],
                    actual={name: value.as_int() for name, value in response.value.items()})
                await FallingEdge(bench.dut.clock)
            else:
                await cache.reset_and_wait_ready(bench.dut)
                cache.set_(bench.dut.io_in_resp_ready, 1)
            cleared = int(flag.value)
            await idle(bench)
            for _ in range(3):
                await FallingEdge(bench.dut.clock)
                assert cache.get(bench.dut.io_in_resp_valid) == 0
            recovery_data = await bench.driver.read(addr + 8)
            await idle(bench)
            bench.observer.checks.check("CACHE-INT-ERROR-FLUSH-LATCH", finish,
                actual={"after_completion": cleared, "recovery_data": recovery_data,
                        "recovery_refills": bench.memory.read_bursts[before_reads:]},
                expected={"after_completion": 0, "recovery_data": bench.backing.read(addr + 8),
                          "recovery_refills": [addr + 8] if finish == "reset" else []},
                tick=int(bench.dut.GetXClock().GetHalfTick()), context={"held": held})
        (bench.output / "faulted-flush-recovery-witness.json").write_text(json.dumps({
            "finish": finish, "injected_needFlush": 1, "held_responses": held, "final_needFlush": int(flag.value),
            "basis": "RTL explicit latch clear on response acceptance or reset; io_flush remains zero",
        }, indent=2) + "\n")
    finally:
        cache.set_(bench.dut.io_in_resp_ready, 1)
        await monitor.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ("empty", "held", "accept_same_cycle"))
async def test_stage2_flush_discards_only_stage2_request(bench, phase):
    """Drive the public flush[0] pin; keep the writable-cache flush[1] low."""
    first, discarded, recovery = 0x18040, 0x18048, 0x18050
    assert await bench.driver.read(first) == bench.backing.read(first)
    await idle(bench)
    monitor = ReadyValidMonitor(bench.interfaces.cpu_response.monitor_view(), capacity=8).start(bench.execution)
    before_reads = len(bench.memory.read_bursts)
    try:
        await FallingEdge(bench.dut.clock)
        if phase == "held":
            async with asyncio.timeout(5):
                await bench.driver.send({"addr": first, "size": 3, "cmd": cache.CMD_READ,
                                         "wmask": 0, "wdata": 0, "user": 0x501})
                await FallingEdge(bench.dut.clock)
                await RisingEdge(bench.dut.clock)  # Stage2 transfers into Stage3.
                await FallingEdge(bench.dut.clock)
                cache.set_(bench.dut.io_in_resp_ready, 0)
                assert cache.get(bench.dut._debug_s3_valid) == 1
                await bench.driver.send({"addr": discarded, "size": 3, "cmd": cache.CMD_READ,
                                         "wmask": 0, "wdata": 0, "user": 0x502})
                await FallingEdge(bench.dut.clock)
            assert bench.observer.snapshot()["valid"] == 1
        cache.set_(bench.dut.io_flush, 1)
        if phase == "accept_same_cycle":
            await bench.driver.send({"addr": discarded, "size": 3, "cmd": cache.CMD_READ,
                                     "wmask": 0, "wdata": 0, "user": 0x502})
        else:
            await RisingEdge(bench.dut.clock)
        await FallingEdge(bench.dut.clock)
        after_flush = bench.observer.snapshot()
        assert after_flush["valid"] == 0
        cache.set_(bench.dut.io_flush, 0)
        if phase == "held":
            assert after_flush["valid_1"] == 1
            assert after_flush["s3.io_in_bits_req_addr"] == first
            cache.set_(bench.dut.io_in_resp_ready, 1)
            async with asyncio.timeout(5):
                response = await monitor.recv()
            Scoreboard("cache.flush-preserves-stage3").check(
                expected={"cmd": cache.CMD_READ_LAST, "rdata": bench.backing.read(first), "user": 0x501},
                actual={name: value.as_int() for name, value in response.value.items()},
            )
        await idle(bench)
        for _ in range(4):
            await FallingEdge(bench.dut.clock)
            assert cache.get(bench.dut.io_in_resp_valid) == 0, "flushed request produced a response"
        assert len(bench.memory.read_bursts) == before_reads
        assert await bench.driver.read(recovery) == bench.backing.read(recovery)
        await idle(bench)
        assert "scenario." + phase not in bench.observer.checks.groups["CACHE-INT-PIPE-S2-FLUSH"].uncovered()
        (bench.output / "flush-witness.json").write_text(json.dumps({
            "phase": phase, "discarded_address": discarded, "after_flush": after_flush,
            "recovery_address": recovery,
        }, indent=2) + "\n")
    finally:
        cache.set_(bench.dut.io_flush, 0)
        cache.set_(bench.dut.io_in_resp_ready, 1)
        await monitor.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("cmd", (cache.CMD_READ, cache.CMD_WRITE))
async def test_mmio_request_survives_downstream_backpressure(bench, cmd):
    addr, data = 0x40000038, 0x123456789abcdef0
    await FallingEdge(bench.dut.clock)
    cache.set_(bench.dut.io_mmio_req_ready, 0)
    pending = asyncio.create_task(bench.driver.request(addr, cmd, wdata=data, wmask=0xa5))
    try:
        async with asyncio.timeout(5):
            await Value(bench.dut._debug_state, 5, sample=FallingEdge(bench.dut.clock))
            for _ in range(4):
                await FallingEdge(bench.dut.clock)
                assert cache.get(bench.dut._debug_state) == 5
                assert cache.get(bench.interfaces.mmio_request.valid) == 1
                bits = bench.interfaces.mmio_request.bits.sample()
                assert {name: value.as_int() for name, value in bits.items()} == {
                    "addr": addr, "cmd": cmd, "size": 3, "wdata": data, "wmask": 0xa5,
                }
                assert not bench.mmio.requests
            # The responder samples ready at FallingEdge; release before its
            # next sample so it observes the same acceptance as the RTL.
            await RisingEdge(bench.dut.clock)
            cache.set_(bench.dut.io_mmio_req_ready, 1)
            response = await pending
        expected = (cache.CMD_READ_LAST, bench.mmio.memory.read(addr)) if cmd == cache.CMD_READ else (cache.CMD_WRITE_RESP, 0)
        Scoreboard("cache.mmio-request-stall").check(expected=expected, actual=response[:2])
        assert len(bench.mmio.requests) == 1
        if cmd == cache.CMD_WRITE:
            reference = cache.ReferenceMemory()
            reference.write(addr, data, 0xa5)
            assert bench.mmio.memory.read(addr) == reference.read(addr)
        await idle(bench)
        assert "scenario.stalled" not in bench.observer.checks.groups["CACHE-INT-FSM-MMIO-REQUEST"].uncovered()
    finally:
        cache.set_(bench.dut.io_mmio_req_ready, 1)
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("start", (0, 7))
async def test_full_cache_read_burst_exposes_premature_retirement(bench, start):
    """Retain a real top-level defect witness, including its state transitions."""
    line = 0x1c040
    assert await bench.driver.read(line) == bench.backing.read(line)
    await idle(bench)
    addr = line + start * 8
    user = bench.driver.user
    before_reads = len(bench.memory.read_bursts)
    response = await bench.driver.request(addr, cache.CMD_READ_BURST, response_stall_cycles=3)
    Scoreboard("cache.burst-first-word").check(
        expected=(cache.CMD_READ_LAST, bench.backing.read(addr), user), actual=response,
    )
    await FallingEdge(bench.dut.clock)
    after_first = bench.observer.snapshot()
    assert after_first["s3.state"] == 8
    assert after_first["s3.readBeatCnt_value"] == (start + 1) & 7
    assert after_first["s3.respToL1Last_c_value"] == 1
    assert after_first["valid_1"] == 0
    trace = []
    for _ in range(24):
        await FallingEdge(bench.dut.clock)
        assert cache.get(bench.dut.io_in_resp_valid) == 0
        snapshot = bench.observer.snapshot()
        trace.append({name: snapshot[name] for name in (
            "s3.state", "s3.state2", "s3.io_cohResp_valid", "s3.io_out_valid", "valid_1",
        )})
    assert len(bench.memory.read_bursts) == before_reads
    assert any(item["s3.io_cohResp_valid"] for item in trace), "missing unexpected coherence output witness"
    actual_beats = 1 + sum(item["s3.io_out_valid"] for item in trace)
    with pytest.raises(AssertionError, match="CACHE-INT-BURST-READ-HIT"):
        bench.observer.checks.check(
            "CACHE-INT-BURST-READ-HIT", f"start{start}",
            expected={"cpu_beats": cache.WORDS_PER_LINE, "retained_after_first": 1},
            actual={"cpu_beats": actual_beats, "retained_after_first": after_first["valid_1"]},
            tick=bench.driver.response_event.tick,
            context={"observation": "burst_retirement", "address": addr, "trace": trace},
        )
    (bench.output / "rtl-defects.json").write_text(json.dumps({
        "defect": "CPU read burst retires its top-level context after the first beat and emits coherence data",
        "source": "example/CacheSignalCFG/Cache.v", "start_word": start,
        "expected_cpu_beats": cache.WORDS_PER_LINE, "actual_cpu_beats": actual_beats,
        "first_response": response, "after_first": after_first, "trace": trace,
    }, indent=2) + "\n")


@pytest.mark.asyncio
async def test_cpu_request_overlaps_remaining_refill(bench):
    first, second = 0x2804e8, 0x2804c8
    assert await bench.driver.read(first) == bench.backing.read(first)
    before = bench.observer.snapshot()
    assert before["s3.state"] == 2 and before["s3.alreadyOutFire"] == 1
    monitor = ReadyValidMonitor(bench.interfaces.cpu_request.monitor_view(), capacity=4).start(bench.execution)
    try:
        user = bench.driver.user
        response = await bench.driver.request(second, cache.CMD_READ, require_idle=False)
        accepted = await monitor.recv()
        Scoreboard("cache.early-refill-overlap").check(
            expected=(cache.CMD_READ_LAST, bench.backing.read(second), user), actual=response,
        )
        assert accepted.value.addr.as_int() == second
        await idle(bench)
        assert bench.memory.read_bursts == [first]
        (bench.output / "overlap-witness.json").write_text(json.dumps({
            "before_second_request": before, "accepted_tick": accepted.event.tick,
            "accepted_address": second, "response": response,
        }, indent=2) + "\n")
    finally:
        await monitor.aclose()


def require(bench, requirements):
    missing = {name: list(bench.observer.checks.groups["CACHE-INT-" + name].uncovered())
               for name in requirements if bench.observer.checks.groups["CACHE-INT-" + name].uncovered()}
    assert not missing, f"uncovered full-Cache scenarios: {missing}"


@pytest.mark.asyncio
async def test_hit_stream_keeps_pipeline_contexts(bench):
    addresses = (0x1000, 0x1008, 0x1040, 0x3000)
    for addr in addresses:
        assert await bench.driver.read(addr) == bench.backing.read(addr)
    await idle(bench)
    monitor = ReadyValidMonitor(bench.interfaces.cpu_response.monitor_view(), capacity=64).start(bench.execution)
    traffic = [addresses[index % len(addresses)] for index in range(24)]
    expected = [(cache.CMD_READ_LAST, bench.backing.read(addr), 0x100 + index) for index, addr in enumerate(traffic)]

    async def collect():
        actual = []
        for _ in traffic:
            item = await monitor.recv()
            actual.append(tuple(item.value[name].as_int() for name in ("cmd", "rdata", "user")))
        return actual

    collecting = asyncio.create_task(collect())
    try:
        async with asyncio.timeout(5):
            for index, addr in enumerate(traffic):
                if index == 1:
                    # Let the first request enter Stage3 before withdrawing
                    # ready; Stage2 can then accept and hold the second one.
                    await FallingEdge(bench.dut.clock)
                    await RisingEdge(bench.dut.clock)
                    await FallingEdge(bench.dut.clock)
                    cache.set_(bench.dut.io_in_resp_ready, 0)
                if index == 2:
                    for _ in range(3):
                        await FallingEdge(bench.dut.clock)
                    cache.set_(bench.dut.io_in_resp_ready, 1)
                await bench.driver.send({"addr": addr, "size": 3, "cmd": cache.CMD_READ,
                                         "wmask": 0xff, "wdata": 0, "user": 0x100 + index})
            actual = await collecting
        Scoreboard("cache.hit-stream").check(expected=expected, actual=actual)
        await idle(bench)
    finally:
        collecting.cancel()
        await asyncio.gather(collecting, return_exceptions=True)
        await monitor.aclose()
    require(bench, ("PIPE-S2-HOLD", "PIPE-S3-HOLD", "PIPE-S2-CONSUME", "PIPE-RETIRE", "PIPE-S1-INDEX"))


@pytest.mark.asyncio
async def test_probe_competes_with_cpu_and_sram(bench):
    line = 0x8040
    assert await bench.driver.read(line) == bench.backing.read(line)
    await idle(bench)
    words = tuple(bench.backing.read(line + word * 8) for word in range(8))
    monitor = ReadyValidMonitor(bench.interfaces.cpu_response.monitor_view(), capacity=8).start(bench.execution)
    probe = asyncio.create_task(bench.coherence.probe(line, line=words, stall_beat=4))
    try:
        await bench.driver.send({"addr": line + 8, "size": 3, "cmd": cache.CMD_READ,
                                 "wmask": 0, "wdata": 0, "user": 0x222})
        async with asyncio.timeout(5):
            response = await monitor.recv()
            await probe
        Scoreboard("cache.probe-cpu").check(expected={"cmd": cache.CMD_READ_LAST, "rdata": words[1], "user": 0x222},
                                           actual={name: value.as_int() for name, value in response.value.items()})
        await idle(bench)
        probe = asyncio.create_task(bench.coherence.probe(line, line=words))
        await Value(bench.dut._debug_state, 8, sample=FallingEdge(bench.dut.clock))
        # Both SRAM request ports are now active at the next acceptance edge.
        await bench.driver.send({"addr": line + 16, "size": 3, "cmd": cache.CMD_READ,
                                 "wmask": 0, "wdata": 0, "user": 0x223})
        async with asyncio.timeout(5):
            response = await monitor.recv()
            await probe
        Scoreboard("cache.release-cpu").check(expected={"cmd": cache.CMD_READ_LAST, "rdata": words[2], "user": 0x223},
                                              actual={name: value.as_int() for name, value in response.value.items()})
        await idle(bench)
    finally:
        probe.cancel()
        await asyncio.gather(probe, return_exceptions=True)
        await monitor.aclose()
    # Per-port progress is checked continuously; require an actual collision.
    group = bench.observer.checks.groups["CACHE-INT-ARBITRATION-PROBE-PRIORITY"]
    assert "scenario.both" not in group.uncovered()
    assert "scenario.both" not in bench.observer.checks.groups["CACHE-INT-ARRAY-DATA-PORT"].uncovered()
    missing = bench.observer.checks.groups["CACHE-INT-ARBITRATION-DATA-PRIORITY"].uncovered()
    assert "scenario.release_wait" not in missing and "scenario.priority_released" not in missing


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ("refill", "writeback"))
async def test_probe_waits_for_cpu_memory_transaction(bench, phase):
    probe_line = 0x8800
    assert await bench.driver.read(probe_line) == bench.backing.read(probe_line)
    await idle(bench)
    if phase == "writeback":
        for tag in range(4):
            addr = 0xf040 + tag * cache.SAME_SET_STRIDE
            await bench.driver.read(addr)
            await bench.driver.write(addr, 0x9876543210000000 | tag)
        await idle(bench)
        cpu_addr, state = 0xf040 + 4 * cache.SAME_SET_STRIDE, 3
    else:
        cpu_addr, state = 0xe040, 2
    words = tuple(bench.backing.read(probe_line + word * 8) for word in range(8))
    cpu = asyncio.create_task(bench.driver.read(cpu_addr))
    probe = None
    try:
        await Value(bench.dut._debug_state, state, sample=FallingEdge(bench.dut.clock))
        before = bench.observer.snapshot()
        probe = asyncio.create_task(bench.coherence.probe(probe_line, line=words, timeout_cycles=300))
        async with asyncio.timeout(5):
            value, _ = await asyncio.gather(cpu, probe)
        Scoreboard("cache.cpu-memory-probe").check(expected=bench.backing.read(cpu_addr), actual=value)
        await idle(bench)
        (bench.output / "concurrent-probe-witness.json").write_text(json.dumps({
            "phase": phase, "before_probe": before, "cpu_address": cpu_addr,
            "cpu_response": value, "probe": bench.coherence.records[-1],
        }, indent=2) + "\n")
    finally:
        tasks = [task for task in (cpu, probe) if task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_refill_finishes_while_cpu_response_is_stalled(bench):
    addr = 0x9000
    assert await bench.driver.read(addr, response_stall_cycles=30) == bench.backing.read(addr)
    await idle(bench)
    group = bench.observer.checks.groups["CACHE-INT-EARLY-FINAL-WHILE-STALLED"]
    group.assert_coverage(100)


@pytest.mark.asyncio
async def test_cpu_read_shares_sram_with_writeback(bench):
    other = 0x10c0
    await bench.driver.read(other)
    for tag in range(4):
        addr = 0x1040 + tag * cache.SAME_SET_STRIDE
        await bench.driver.read(addr)
        await bench.driver.write(addr, 0x1020304050607080 | tag)
    await idle(bench)
    monitor = ReadyValidMonitor(bench.interfaces.cpu_response.monitor_view(), capacity=8).start(bench.execution)
    miss_addr = 0x1040 + 4 * cache.SAME_SET_STRIDE
    miss = asyncio.create_task(bench.driver.read(miss_addr))
    try:
        await Value(bench.dut._debug_state, 3, sample=FallingEdge(bench.dut.clock))
        await bench.driver.send({"addr": other, "size": 3, "cmd": cache.CMD_READ,
                                 "wmask": 0, "wdata": 0, "user": 0x444})
        async with asyncio.timeout(5):
            first, second = await monitor.recv(), await monitor.recv()
            value = await miss
        assert value == bench.backing.read(miss_addr)
        Scoreboard("cache.writeback-cpu").check(
            expected=[bench.backing.read(miss_addr), bench.backing.read(other)],
            actual=[first.value.rdata.as_int(), second.value.rdata.as_int()],
        )
        assert second.value.user.as_int() == 0x444
        await idle(bench)
    finally:
        miss.cancel()
        await asyncio.gather(miss, return_exceptions=True)
        await monitor.aclose()
    assert "scenario.writeback_wait" not in bench.observer.checks.groups["CACHE-INT-ARBITRATION-DATA-PRIORITY"].uncovered()


@pytest.mark.asyncio
async def test_cpu_checker_rejects_valid_withdrawal(bench, monkeypatch):
    bench.observer.checks.evidence_kind = "checker_counterexample"
    addr = 0x11040
    await bench.driver.read(addr)
    await idle(bench)
    original = cache.get
    injected = False

    def altered(signal):
        nonlocal injected
        value = original(signal)
        if (not injected and signal is bench.driver.response_interface.valid and value
                and not original(bench.driver.response_interface.ready)):
            injected = True
            return 0
        return value

    monkeypatch.setattr(cache, "get", altered)
    with pytest.raises(AssertionError, match="CPU response valid withdrawn under backpressure"):
        await bench.driver.read(addr, response_stall_cycles=3)
    assert injected


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ("refill", "writeback", "release"))
async def test_reset_discards_inflight_cache_control(bench, phase):
    active = None
    if phase == "writeback":
        for tag in range(4):
            addr = 0xa040 + tag * cache.SAME_SET_STRIDE
            await bench.driver.read(addr)
            await bench.driver.write(addr, 0x1234567800000000 | tag)
        await idle(bench)
        active = asyncio.create_task(bench.driver.read(0xa040 + 4 * cache.SAME_SET_STRIDE))
        state = 3
    elif phase == "release":
        line = 0xb040
        await bench.driver.read(line)
        await idle(bench)
        words = tuple(bench.backing.read(line + word * 8) for word in range(8))
        active = asyncio.create_task(bench.coherence.probe(line, line=words))
        state = 8
    else:
        active = asyncio.create_task(bench.driver.read(0xc040))
        state = 2
    try:
        await Value(bench.dut._debug_state, state, sample=FallingEdge(bench.dut.clock))
        active.cancel()
        await asyncio.gather(active, return_exceptions=True)
        # Reset cancels the external memory transaction too. Replace the
        # responder after joining it so no queued pre-reset beat can leak.
        old_memory_task = bench.tasks[0]
        old_memory_task.cancel()
        await asyncio.gather(old_memory_task, return_exceptions=True)
        bench.memory = cache.MemoryModel(bench.dut, bench.backing, bench.interfaces, response_gap=1)
        bench.tasks[0] = asyncio.create_task(bench.memory.run())
        await cache.reset_and_wait_ready(bench.dut)
        addr = 0xd040
        assert await bench.driver.read(addr) == bench.backing.read(addr)
        await idle(bench)
    finally:
        active.cancel()
        await asyncio.gather(active, return_exceptions=True)
    assert "scenario." + phase not in bench.observer.checks.groups["CACHE-INT-RESET-CONTROL"].uncovered()
