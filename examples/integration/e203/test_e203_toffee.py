from __future__ import annotations

import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path

import pytest

import e203_toffee_env as env
from e203_toffee_env import (
    E203Harness,
    FetchRequest,
    convert_line_coverage,
    directed_requests,
    load_generated_dut,
    run_campaign,
)
from toffee import (
    Execution,
    ScoreboardMismatch,
    XCommClockBackend,
    generate_unified_coverage_report,
    generate_unified_coverage_site,
)


REPOSITORY = Path(__file__).resolve().parents[3]


@pytest.mark.asyncio
async def test_e203_reference_stress_and_coverage() -> None:
    generated = Path(
        os.environ.get("E203_DUT_DIR", REPOSITORY / "output" / "toffee_e203")
    ).resolve()
    artifacts = Path(
        os.environ.get(
            "E203_ARTIFACT_DIR",
            REPOSITORY / "output" / "e203-verification",
        )
    ).resolve()
    seed = int(os.environ.get("E203_SEED", "0xE203"), 0)
    random_count = int(os.environ.get("E203_RANDOM_COUNT", "500"), 0)
    minimum_tps = float(os.environ.get("E203_MIN_TRANSACTIONS_PER_SECOND", "0"))
    timeout = float(os.environ.get("E203_WALL_TIMEOUT", "60"))
    if random_count < 0:
        raise ValueError("E203_RANDOM_COUNT must not be negative")

    # Verilator's Finish() writes here and aborts if the parent is missing.
    artifacts.mkdir(parents=True, exist_ok=True)
    coverage_dat = artifacts / "verilator-coverage.dat"
    DUT = load_generated_dut(generated)
    dut = DUT(coverage_filename=str(coverage_dat))
    harness = None
    summary = None
    try:
        async with asyncio.timeout(timeout):
            harness, summary = await run_campaign(
                dut,
                seed=seed,
                random_count=random_count,
                artifact_dir=artifacts,
                data_pattern=os.environ.get("E203_DATA_PATTERN", "address"),
            )
    finally:
        # Verilator writes its code-coverage database during Finish().
        dut.Finish()

    assert harness is not None and summary is not None
    assert summary["transactions"] == len(directed_requests()) + random_count
    assert summary["checked"] == summary["transactions"]
    assert summary["commands"] > 0
    assert summary["half_ticks"] > summary["transactions"]
    assert summary["transactions_per_second"] >= minimum_tps
    harness.coverage.assert_coverage(100.0)

    # Require witnesses in the deterministic prefix, so random traffic cannot
    # conceal a deleted scenario while the campaign's feature labels still pass.
    directed = harness.transactions[:len(directed_requests())]
    by_pc = {item.pc: item for item in directed}
    transition = [by_pc[0x8000_6000 + offset] for offset in (0, 2, 4, 6, 8, 0x42)]
    checks = {
        "ROUTE-ITCM": {item.alignment for item in directed
                       if item.target == "itcm" and item.command_count == 1} >= {"lane_begin", "middle"},
        "ROUTE-BIU": any(item.target == "biu" and item.command_count == 1 for item in directed),
        "SPLIT-ITCM": any(item.target == "itcm" and item.command_count == 2 for item in directed),
        "SPLIT-BIU": any(item.target == "biu" and item.command_count == 2 for item in directed),
        "REGION-SWITCH": all(by_pc[pc].route_switch for pc in (0x8000_FFFE, 0x7FFF_FFFE)),
        "ADDRESS-WRAP": by_pc[0xFFFF_FFFE].command_count == 2,
        "HOLD-REUSE": {item.command_count for item in directed if item.holdup} >= {0, 1},
        "SEQUENTIAL-2": {item.lane_cross for item in directed
                         if item.sequential and not item.seq_rv32} == {False, True},
        "SEQUENTIAL-4": by_pc[0x8000_0506].seq_rv32 and by_pc[0x8000_0506].command_count == 1
                        and by_pc[0x8000_0508].command_count == 0,
        "NOHOLD-TRANSITION": [item.command_count for item in transition] == [1, 0, 1, 2, 0, 1]
                             and [item.response for item in transition] == ["ok", "ok", "ok", "error", "ok", "ok"],
        "ERROR-OR": {(item.target, item.error_pattern) for item in directed if item.command_count == 2}
                    >= {(target, errors) for target in ("itcm", "biu") for errors in range(4)},
        "COMMAND-STALL": any(command.ordinal == 0 and command.stalled
                             for command in harness.commands if command.tick <= directed[-1].completed_tick),
        "SECOND-COMMAND-STALL": any(item.second_command_backpressure for item in directed),
        "OUTPUT-STALL": any(item.response_backpressure for item in directed),
        "SPLIT-DELAY": {(item.target, item.response_delay_order) for item in directed
                        if item.command_count == 2 and item.response_backpressure and item.second_command_backpressure}
                       >= {(target, order) for target in ("itcm", "biu")
                           for order in ("first_slower", "second_slower")},
    }
    assert all(checks.values()), f"missing directed feature evidence: {[key for key, passed in checks.items() if not passed]}"

    functional_path = artifacts / "functional-coverage.json"
    functional = json.loads(functional_path.read_text(encoding="utf-8"))
    assert functional["coverage"] == pytest.approx(100.0)
    functional_group = functional["groups"][0]
    assert functional_group["covered"] is True
    assert all(item["coverage"] == pytest.approx(100.0) for item in functional_group["points"])
    assert all(item["coverage"] == pytest.approx(100.0) for item in functional_group["crosses"])

    line_info, line_summary = convert_line_coverage(coverage_dat, artifacts)
    assert line_info.is_file()
    assert line_summary["lines_found"] > 0
    assert line_summary["lines_hit"] > 0
    assert 0.0 < line_summary["line_coverage"] <= 100.0
    report = generate_unified_coverage_report(
        functional_paths=[functional_path],
        line_coverage=[("Verilator", line_info)],
        output=artifacts / "coverage-report.html",
        title="e203 IFU-to-ICB Coverage",
    )
    assert report.is_file()
    assert report.stat().st_size > 10_000
    site = generate_unified_coverage_site(
        functional_paths=[functional_path],
        line_coverage=[("Verilator", line_info)],
        output_dir=artifacts / "coverage-report",
        title="e203 IFU-to-ICB Coverage",
    )
    assert site.is_file()
    source_pages = list((artifacts / "coverage-report" / "line").rglob("*.gcov.html"))
    assert source_pages
    assert any("e203_ifu_ift2icb.v" in path.name for path in source_pages)
    target_page = next(
        path for path in source_pages if "e203_ifu_ift2icb.v" in path.name
    )
    assert "Unified coverage overview" in target_page.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_e203_missing_response_times_out() -> None:
    """A permanently delayed ICB response must fail with execution context."""

    generated = Path(
        os.environ.get("E203_DUT_DIR", REPOSITORY / "output" / "toffee_e203")
    ).resolve()
    artifacts = Path(
        os.environ.get(
            "E203_ARTIFACT_DIR",
            REPOSITORY / "output" / "e203-verification",
        )
    ).resolve()
    artifacts.mkdir(parents=True, exist_ok=True)

    DUT = load_generated_dut(generated)
    dut = DUT(coverage_filename=str(artifacts / "timeout-coverage.dat"))
    dut.InitClock("clk")
    harness = E203Harness(dut, seed=0xBAD)
    harness.initialize_inputs()
    backend = XCommClockBackend(dut.GetXClock())
    try:
        async with Execution(backend, max_batch_ticks=128, quantum_ms=5.0) as execution:
            harness.internal.start(execution)
            await harness.reset()
            with pytest.raises(TimeoutError, match=r"timed out after 5 cycles"):
                await harness.fetch(
                    FetchRequest(0x0001_0020, response_delay=1_000),
                    timeout_cycles=5,
                )
            await harness.reset()
            transaction = await harness.fetch(FetchRequest(0x8000_0100))
            assert transaction.instruction == harness.memory.instruction(0x8000_0100)
            assert harness.scoreboard.status.checked == 1
            await harness.finish()
    finally:
        try:
            backend.close()
        finally:
            dut.Finish()


@pytest.fixture
def native_case(tmp_path, monkeypatch):
    """Keep each focused test's artifacts and inspect native cleanup before Finish."""
    generated = Path(os.environ.get("E203_DUT_DIR", REPOSITORY / "output/toffee_e203"))
    DUT = load_generated_dut(generated)
    dut = DUT(coverage_filename=str(tmp_path / "verilator-coverage.dat"))
    closed = []

    class AuditedBackend(XCommClockBackend):
        def close(self):
            closed.append((self.watcher_count, self._owner, tuple(
                int(signal.value) for signal in (dut.ifu_req_valid, dut.ifu2itcm_icb_rsp_valid,
                                                dut.ifu2biu_icb_rsp_valid)
            )))
            super().close()

    monkeypatch.setattr(env, "XCommClockBackend", AuditedBackend)

    async def campaign(**options):
        before = asyncio.all_tasks()
        try:
            return await run_campaign(dut, seed=1, random_count=0,
                                      artifact_dir=tmp_path, **options)
        finally:
            assert asyncio.all_tasks() == before, "e203 left background tasks after exit"

    try:
        yield dut, tmp_path, campaign
    finally:
        dut.Finish()
        assert closed and all(count == 0 and owner is None and pins == (0, 0, 0)
                              for count, owner, pins in closed)


@pytest.mark.asyncio
@pytest.mark.parametrize("pattern", [
    pytest.param(pattern, id=pattern)
    for pattern in ("zero", "ones", "alternating", "walking")
])
async def test_e203_data_patterns(native_case, pattern):
    _, output, campaign = native_case
    harness, summary = await campaign(data_pattern=pattern)
    assert summary["checked"] == len(directed_requests())
    assert summary["data_pattern"] == pattern
    harness.coverage.assert_coverage(100.0)
    assert json.loads((output / "commands.json").read_text())
    assert {transaction.command_count for transaction in harness.transactions} == {0, 1, 2}


@pytest.mark.asyncio
async def test_e203_internal_scenarios(native_case):
    """Close RTL mechanism bins with observed accepts, not case annotations."""
    dut, output, _ = native_case
    dut.InitClock("clk")
    harness = E203Harness(dut)
    harness.initialize_inputs()
    backend = env.XCommClockBackend(dut.GetXClock())
    base = env.ITCM_BASE + 0x8000
    try:
        async with Execution(backend) as execution:
            harness.internal.start(execution)
            try:
                await harness.reset()
                for request in directed_requests():
                    await harness.fetch(request)
                for request in [
                    FetchRequest(base + 4),
                    FetchRequest(base + 8, sequential=True, seq_rv32=True, last_pc=base + 4),
                    FetchRequest(base + 0x106, second_command_stall=5),
                    FetchRequest(base + 0x200, response_stall=1, error_uops=frozenset({0})),
                    FetchRequest(base + 0x306, error_uops=frozenset({0})),
                    FetchRequest(base + 0x30a, sequential=True, seq_rv32=True,
                                 last_pc=base + 0x306, holdup=True),
                    FetchRequest(base + 0x30e, sequential=True, seq_rv32=True,
                                 last_pc=base + 0x30a, holdup=True),
                    FetchRequest(base + 0x310, unselected_response=True),
                    FetchRequest(0x4310, unselected_response=True),
                ]:
                    await harness.fetch(request)

                harness.command_ready_targets = frozenset({"itcm"})
                await harness.fetch(FetchRequest(base + 0x400))
                await harness.fetch(FetchRequest(base + 0x402, sequential=True,
                                                last_pc=base + 0x400, holdup=True, command_stall=2))
                harness.command_ready_targets = frozenset({"biu"})
                await harness.fetch(FetchRequest(0x4000))
                harness.command_ready_targets = frozenset({"itcm", "biu"})

                # First→first: zero→one, one→split and target change.
                await harness.stream_fetches([
                    FetchRequest(base + 0x500),
                    FetchRequest(base + 0x502, sequential=True, last_pc=base + 0x500, holdup=True),
                    FetchRequest(base + 0x504), FetchRequest(base + 0x506),
                    FetchRequest(base + 0x508, sequential=True, last_pc=base + 0x506, holdup=True),
                    FetchRequest(0x5000), FetchRequest(base + 0x510),
                    FetchRequest(base + 0x516), FetchRequest(base + 0x526), FetchRequest(0x5010),
                ])
                # Keep the old error response buffered while a zero-uop
                # response with different bytes/error is waiting behind it.
                await harness.stream_fetches([
                    FetchRequest(base + 0x600, error_uops=frozenset({0})),
                    FetchRequest(base + 0x602, sequential=True, last_pc=base + 0x600, holdup=True),
                ], consumer_stall=12)
                await harness.finish()

                # Reset an accepted request before its delayed response.
                with pytest.raises(TimeoutError):
                    await harness.fetch(FetchRequest(base + 0x700, response_delay=100), timeout_cycles=3)
                await harness.reset()
                await harness.fetch(FetchRequest(base + 0x800))
                await harness.finish()
            finally:
                await harness.internal.aclose()
    finally:
        harness.idle_inputs()
        backend.close()
        harness.internal.checks.write(output)
        (output / "stream-transactions.json").write_text(json.dumps(harness.stream_records, indent=2) + "\n")
    assert not harness.internal.checks.failures
    missing = {name: list(group.uncovered()) for name, group in harness.internal.checks.groups.items()
               if group.uncovered()}
    assert not missing, f"uncovered e203 RTL scenarios: {missing}"


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ("first_response", "second_command", "buffered_output"))
async def test_e203_reset_discards_inflight_context(native_case, monkeypatch, phase):
    dut, output, _ = native_case
    dut.InitClock("clk")
    harness = E203Harness(dut)
    harness.initialize_inputs()
    backend = env.XCommClockBackend(dut.GetXClock())
    cycle_once = harness._cycle_once
    witness = None

    class ResetPhaseReached(Exception):
        pass

    async def stop_at_phase():
        nonlocal witness
        step = await cycle_once()
        snapshot = harness.internal.snapshot()
        reached = ((phase == "first_response" and step["command"] is not None
                    and snapshot["state"] == 1)
                   or (phase == "second_command" and snapshot["state"] == 2)
                   or (phase == "buffered_output" and snapshot["fifo_full"] == 1
                       and step["output_valid"] and not step["output_accept"]))
        if reached:
            witness = {"phase": phase, "tick": step["event"].tick, "before_reset": snapshot}
            raise ResetPhaseReached
        return step

    base = env.ITCM_BASE + 0xa000
    request = {"first_response": FetchRequest(base, response_delay=30),
               "second_command": FetchRequest(base + 6, second_command_stall=30),
               "buffered_output": FetchRequest(base, response_stall=30)}[phase]
    try:
        async with Execution(backend) as execution:
            harness.internal.start(execution)
            try:
                await harness.reset()
                monkeypatch.setattr(harness, "_cycle_once", stop_at_phase)
                with pytest.raises(ResetPhaseReached):
                    await harness.fetch(request)
                monkeypatch.setattr(harness, "_cycle_once", cycle_once)
                await harness.reset()
                witness["after_reset"] = harness.internal.snapshot()
                recovered = await harness.fetch(FetchRequest(base + 0x106))
                assert recovered.response == "ok"
                await harness.finish()
            finally:
                await harness.internal.aclose()
    finally:
        harness.idle_inputs()
        backend.close()
        harness.internal.checks.write(output)
        (output / "reset-witness.json").write_text(json.dumps(witness, indent=2) + "\n")
    assert witness is not None and not harness.internal.checks.failures
    assert not harness.internal.checks.observer_errors
    scenario = "buffered_reset" if phase == "buffered_output" else "inflight_reset"
    assert "scenario." + scenario not in harness.internal.checks.groups["E203-INT-RESET-CONTROL"].uncovered()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault, requirement", [
    ("context", "CTX-REQ-LOAD"), ("leftover", "LEFTOVER-FIRST-LOAD"),
    ("state", "FSM-FIRST-WAIT"), ("buffer", "BUFFER-CAPTURE"),
])
async def test_e203_internal_checks_reject_counterexamples(native_case, monkeypatch, fault, requirement):
    dut, output, _ = native_case
    dut.InitClock("clk")
    harness = E203Harness(dut)
    harness.internal.checks.evidence_kind = "checker_counterexample"
    harness.initialize_inputs()
    backend = env.XCommClockBackend(dut.GetXClock())
    observe = harness.internal.observe
    injected = False

    def altered(p, q, tick):
        nonlocal injected
        if not injected and p["rst"]:
            q = dict(q)
            if fault == "context" and p["req_v"] and p["req_r"]:
                q["need0_r"] ^= 1
                injected = True
            elif fault == "leftover" and p["state"] == 1 and p["need2_r"] and p["rsp_v"] and p["rsp_r"]:
                q["left"] ^= 1
                injected = True
            elif fault == "state" and p["state"] == 1 and p["need2_r"] and p["rsp_v"] and p["rsp_r"] and not p["cmd_r"]:
                q["state"] = 3
                injected = True
            elif fault == "buffer" and p["fifo_full"] and p["out_r"]:
                p = {**p, "out_data": p["out_data"] ^ 1}
                injected = True
        observe(p, q, tick)

    monkeypatch.setattr(harness.internal, "observe", altered)
    try:
        with pytest.raises(AssertionError, match="E203-INT-" + requirement):
            async with Execution(backend) as execution:
                harness.internal.start(execution)
                try:
                    await harness.reset()
                    await harness.fetch(FetchRequest(env.ITCM_BASE + 0x106,
                                                     second_command_stall=3, response_stall=3))
                finally:
                    await harness.internal.aclose()
    finally:
        harness.idle_inputs()
        backend.close()
        harness.internal.checks.write(output)
    assert injected and len(harness.internal.checks.failures) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("pc", [
    pytest.param(pc, id=name.lower())
    for name, pc in (("ITCM", 0x8000_0100), ("ITCM-SPLIT", 0x8000_0106),
                     ("BIU", 0x100), ("BIU-SPLIT", 0x102))
])
async def test_e203_exact_response_budget(native_case, pc):
    dut, _, _ = native_case
    dut.InitClock("clk")
    harness = E203Harness(dut)
    harness.initialize_inputs()
    backend = env.XCommClockBackend(dut.GetXClock())
    request = FetchRequest(pc, command_stall=2, second_command_stall=2,
                           response_delay=1, second_response_delay=3, response_stall=2)
    try:
        async with Execution(backend) as execution:
            harness.internal.start(execution)
            await harness.reset()
            baseline = await harness.fetch(request)
            await harness.reset()
            exact = await harness.fetch(request, timeout_cycles=baseline.latency)
            assert exact.latency == baseline.latency
            await harness.reset()
            with pytest.raises(TimeoutError, match=f"after {baseline.latency - 1} cycles"):
                await harness.fetch(request, timeout_cycles=baseline.latency - 1)
            assert harness.active_request["accepted_tick"] is not None
            await harness.reset()
            assert (await harness.fetch(FetchRequest(pc))).response == "ok"
            await harness.finish()
    finally:
        harness.idle_inputs()
        backend.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault, error, message", [
    pytest.param(fault, error, message, id=fault)
    for fault, error, message in (
    ("instruction", ScoreboardMismatch, "instruction"),
    ("error", ScoreboardMismatch, "error"),
    ("command_address", ScoreboardMismatch, "commands"),
    ("missing_command", ScoreboardMismatch, "commands"),
    ("missing_response", TimeoutError, "timed out"),
    ("extra_response", AssertionError, "extra IFU response"),
    ("extra_command", AssertionError, "extra ICB command"),
    ("unstable", AssertionError, "changed under backpressure"),
    ("withdrawn", AssertionError, "withdrawn under backpressure"),
    ("early", AssertionError, "before request acceptance"),
    )
])
async def test_e203_checker_rejects_faults(native_case, monkeypatch, fault, error, message):
    _, output, campaign = native_case
    request = FetchRequest(0x8000_7006, command_stall=2, response_stall=3)
    monkeypatch.setattr(env, "directed_requests", lambda: [request])
    cycle_once = E203Harness._cycle_once
    seen_output = False
    injected = False

    async def altered(harness):
        nonlocal seen_output, injected
        step = await cycle_once(harness)
        if harness.transactions:
            if fault == "extra_response":
                step["output_valid"] = True
            if fault == "extra_command":
                step["command"] = harness.commands[-1]
        if harness.active_request is None:
            return step
        if step["command"] is not None:
            if fault == "command_address":
                harness.commands[-1] = replace(harness.commands[-1], address=step["command"].address ^ 4)
            if fault == "missing_command":
                harness.commands.pop()
        if fault == "early" and not step["request_accept"] and not seen_output:
            step.update(output_valid=True, output_accept=True, output=(0, 0))
        if step["output_valid"]:
            value, error_bit = step["output"]
            if fault == "instruction":
                step["output"] = (value ^ 1, error_bit)
            elif fault == "error":
                step["output"] = (value, error_bit ^ 1)
            elif fault == "missing_response":
                step.update(output_valid=False, output_accept=False, output=None)
            elif fault == "unstable" and seen_output:
                step["output"] = (value ^ 1, error_bit)
            elif fault == "withdrawn" and seen_output and not injected:
                step.update(output_valid=False, output_accept=False, output=None)
                injected = True
            seen_output = True
        return step

    monkeypatch.setattr(E203Harness, "_cycle_once", altered)
    with pytest.raises(error, match=message):
        await campaign()
    summary = json.loads((output / "performance.json").read_text())
    assert summary["state"] == "failed" and error.__name__ in summary["error"]
    assert (output / "functional-coverage.json").is_file()
    assert (output / "commands.json").is_file()
    if fault == "missing_response":
        assert summary["active_request"]["accepted_tick"] is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", [
    pytest.param(stage, id=stage)
    for stage in ("before_accept", "waiting_response", "output_held")
])
async def test_e203_external_cancellation(native_case, monkeypatch, stage):
    _, output, campaign = native_case
    request = FetchRequest(0x8000_7006, command_stall=3,
                           response_delay=5, response_stall=5)
    monkeypatch.setattr(env, "directed_requests", lambda: [request])
    cycle_once = E203Harness._cycle_once
    task = None

    async def cancel_at_stage(harness):
        step = await cycle_once(harness)
        if harness.active_request is not None:
            stop = ((stage == "before_accept" and not step["request_accept"])
                    or (stage == "waiting_response" and step["command"] is not None)
                    or (stage == "output_held" and step["output_valid"]))
            if stop:
                task.cancel()
        return step

    monkeypatch.setattr(E203Harness, "_cycle_once", cancel_at_stage)
    host = asyncio.create_task(asyncio.Event().wait())
    try:
        task = asyncio.create_task(campaign())
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not host.done()
        summary = json.loads((output / "performance.json").read_text())
        assert summary["state"] == "failed" and "CancelledError" in summary["error"]
    finally:
        host.cancel()
        await asyncio.gather(host, return_exceptions=True)
