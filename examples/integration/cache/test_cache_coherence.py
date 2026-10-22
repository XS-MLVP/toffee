"""Real-DUT campaign plus faults injected at the coherence Monitor boundary.

Run explicitly with the generated Cache package on PYTHONPATH. Faults alter
testbench observations, never RTL or the common framework's protocol rules.
"""

import asyncio
from dataclasses import replace
import json

import pytest

import cache_functional_toffee as cache
from toffee import BundleValue, ScoreboardMismatch


@pytest.fixture
def audit_cleanup(monkeypatch):
    agents, closed = [], []
    initialize = cache.CoherenceAgent.__init__

    def register(agent, interfaces):
        initialize(agent, interfaces)
        agents.append(agent)

    class AuditedBackend(cache.XCommClockBackend):
        def close(self):
            # Read native state before DUT.Finish(), while its clock is alive.
            closed.append((self.watcher_count, self._owner, [
                (cache.get(agent.driver.request.valid), cache.get(agent.response.ready))
                for agent in agents
            ]))
            super().close()

    monkeypatch.setattr(cache.CoherenceAgent, "__init__", register)
    monkeypatch.setattr(cache, "XCommClockBackend", AuditedBackend)
    yield
    assert closed and all(count == 0 and owner is None and pins == [(0, 0)]
                          for count, owner, pins in closed)
    assert all(agent.monitors["release"]._closed and not agent.driver._claimed for agent in agents)
    assert all(not agent._execution.reactor._drive_owners for agent in agents)


@pytest.mark.asyncio
async def test_real_cache_coherence_campaign(tmp_path, audit_cleanup):
    before = asyncio.all_tasks()
    await cache.main(1, 0, allow_known_bugs=False, artifacts=tmp_path)
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["coherence_probes"] == 18
    assert summary["coherence_beats"] == 146
    assert summary["coherence_coverage"] == 100.0
    records = json.loads((tmp_path / "coherence-transactions.json").read_text())
    assert all(record["state"] == "passed" for record in records)
    assert [len(record["responses"]) for record in records] == [1, 1] + [9] * 16
    for group in (records[2:10], records[10:18]):  # clean, then dirty
        assert {(record["address"] >> 3) & 7 for record in group} == set(range(8))
        assert {record["stall_beat"] for record in group} >= {None, 0, 1, 4, 8}
    assert asyncio.all_tasks() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("fault, error, message", [
    pytest.param(fault, error, message, id=fault)
    for fault, error, message in (
    ("header", ScoreboardMismatch, "cmd"),
    ("data", ScoreboardMismatch, "data"),
    ("last", ScoreboardMismatch, "cmd"),
    ("missing", cache.CacheResponseTimeout, "received 8/9 beats"),
    ("duplicate", AssertionError, "unexpected coherence response"),
    ("stale", AssertionError, "precedes this probe"),
    )
])
async def test_coherence_rejects_faulty_observations(
    tmp_path, monkeypatch, audit_cleanup, fault, error, message,
):
    initialize = cache.CoherenceAgent.__init__

    def inject(agent, interfaces):
        initialize(agent, interfaces)
        monitor = agent.monitors["release"]
        receive = monitor.recv
        injected, duplicate = False, None

        async def altered():
            nonlocal injected, duplicate
            if duplicate is not None:
                result, duplicate = duplicate, None
                return result
            observed = await receive()
            cmd = observed.value.cmd.as_int()
            if injected:
                return observed
            if fault == "duplicate":
                duplicate, injected = observed, True
                return observed
            if fault in ("data", "missing") and cmd != cache.CMD_RELEASE:
                return observed
            if fault == "last" and cmd != cache.CMD_READ_LAST:
                return observed
            injected = True
            if fault == "stale":
                return replace(observed, event=replace(observed.event, tick=0))
            if fault == "missing":
                return await receive()
            field = "rdata" if fault == "data" else "cmd"
            value = observed.value[field].as_int() ^ 1
            fields = tuple((name, replace(item, value=value) if name == field else item)
                           for name, item in observed.value.items())
            return replace(observed, value=BundleValue(fields))

        monkeypatch.setattr(monitor, "recv", altered)

    monkeypatch.setattr(cache.CoherenceAgent, "__init__", inject)
    before = asyncio.all_tasks()
    # Known CPU-overlap exceptions must never suppress a coherence failure.
    with pytest.raises(error, match=message):
        await cache.main(1, 0, allow_known_bugs=True, artifacts=tmp_path)
    assert json.loads((tmp_path / "run.json").read_text())["state"] == "failed"
    assert json.loads((tmp_path / "coherence-transactions.json").read_text())[-1]["state"] == "failed"
    assert (tmp_path / "functional-coverage.json").is_file()
    assert asyncio.all_tasks() == before


@pytest.mark.asyncio
async def test_cancel_during_release(tmp_path, monkeypatch, audit_cleanup):
    initialize = cache.CoherenceAgent.__init__
    task = None

    def interrupt(agent, interfaces):
        initialize(agent, interfaces)
        monitor = agent.monitors["release"]
        receive = monitor.recv

        async def cancel_after_data():
            observed = await receive()
            if observed.value.cmd.as_int() == cache.CMD_RELEASE:
                task.cancel()
            return observed

        monkeypatch.setattr(monitor, "recv", cancel_after_data)

    monkeypatch.setattr(cache.CoherenceAgent, "__init__", interrupt)
    host = asyncio.create_task(asyncio.Event().wait())
    try:
        before = asyncio.all_tasks()
        task = asyncio.create_task(cache.main(1, 0, allow_known_bugs=False, artifacts=tmp_path))
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not host.done()
        assert asyncio.all_tasks() == before
        metadata = json.loads((tmp_path / "run.json").read_text())
        assert metadata["state"] == "failed" and "CancelledError" in metadata["error"]
    finally:
        host.cancel()
        await asyncio.gather(host, return_exceptions=True)
