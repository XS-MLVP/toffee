"""Transaction diagnostics and terminal states remain observable and deterministic."""

import asyncio
from dataclasses import dataclass

import pytest

from toffee import Execution, LogicValue, MemoryBackend, Scoreboard, ScoreboardMismatch, XTransfer
from toffee.external import external_event
from toffee.scoreboard import ScoreboardMode, structural_differences
from toffee.transfers import MISSING, TransferState


@dataclass
class Record:
    value: object


@pytest.mark.parametrize("expected,actual,path", [
    (Record(1), {"value": 1}, ""),
    ({"x": 1}, [], ""),
    ({"x": 1}, {}, "x"),
    ({}, {"x": 1}, "x"),
    ([1], "1", ""),
    ([1], [], "[0]"),
    ([], [1], "[0]"),
    (Record([1]), Record([2]), "value[0]"),
])
def test_structural_comparison_reports_the_actual_mismatched_leaf(expected, actual, path):
    differences = structural_differences(expected, actual)
    assert len(differences) == 1 and differences[0].path == path
    with pytest.raises(ScoreboardMismatch) as caught:
        Scoreboard("nested").check(expected=expected, actual=actual)
    assert caught.value.differences == differences


def test_comparison_bounds_output_and_handles_objects_that_cannot_compare():
    class Opaque:
        def __eq__(self, other):
            raise RuntimeError("no equality operation")

    assert len(structural_differences([1, 2, 3], [4, 5, 6], limit=1)) == 1
    assert len(structural_differences({"a": 1, "b": 2}, {"a": 3, "b": 4}, limit=1)) == 1
    assert len(structural_differences(Record([1, 2]), Record([3, 4]), limit=1)) == 1
    assert len(structural_differences(Opaque(), Opaque())) == 1
    assert repr(MISSING) == "MISSING"
    for value in (LogicValue(2, 0, 4), LogicValue(2, 1, 4)):
        with pytest.raises(ScoreboardMismatch) as caught:
            Scoreboard("logic").check(expected=value, actual=LogicValue(3, 0, 4))
        assert "4" in str(caught.value) and "0x" in str(caught.value)


@pytest.mark.parametrize("kwargs,message", [
    ({"name": ""}, "name"),
    ({"max_differences": 0}, "max_differences"),
    ({"capacity": 0}, "capacity"),
    ({"expected": 1}, "synchronous"),
])
def test_scoreboard_rejects_invalid_configuration(kwargs, message):
    with pytest.raises((TypeError, ValueError), match=message):
        Scoreboard(**({"name": "test"} | kwargs))


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs,message", [
    ({"latency_cycles": -1}, "non-negative"),
    ({"latency_cycles": 6}, "exceeds"),
    ({"sampled": True}, "requires latency"),
    ({"request_key": lambda r: r}, "together"),
    ({"latency_cycles": 1, "request_key": lambda r: r, "response_key": lambda r: r}, "exclusive"),
])
async def test_invalid_response_association_does_not_leave_watchers(kwargs, message):
    backend = MemoryBackend(object())
    scoreboard = Scoreboard("association")
    assert scoreboard.mode is ScoreboardMode.DIRECT
    async with Execution(backend) as execution:
        with pytest.raises(ValueError, match=message):
            scoreboard.bind(execution, driver=object(), monitor=object(), clock=backend.clock,
                            response_timeout_cycles=5, **kwargs)
        assert backend.watcher_count == 0
    await scoreboard.__aexit__(None, None, None)
    with pytest.raises(RuntimeError, match="closed"):
        await scoreboard.__aenter__()
    with pytest.raises(RuntimeError, match="submissions"):
        scoreboard.submit(1)


@pytest.mark.asyncio
async def test_transfer_terminal_state_callbacks_are_immediate_and_idempotent():
    transfer = XTransfer("request")
    event = external_event("accepted")
    transfer.set_expected(None)
    with pytest.raises(RuntimeError, match="already has"):
        transfer.set_expected(1)
    transfer.mark_processing(event)
    with pytest.raises(RuntimeError, match="PROCESSING"):
        transfer.mark_processing(event)
    transfer.observe_failure()  # successful work has nothing to acknowledge
    transfer.complete(event, 3)
    calls = []
    transfer.add_done_callback(lambda item: calls.append(item))
    transfer.fail(ValueError("late failure"))
    transfer.cancel()
    assert calls == [transfer] and await transfer == 3
    assert "COMPLETED" in repr(transfer)
    with pytest.raises(RuntimeError, match="completion"):
        transfer.set_expected(4)


@pytest.mark.asyncio
async def test_acceptance_failure_is_observed_once_and_late_observers_are_notified():
    transfer = XTransfer("failed")
    error = ValueError("rejected before acceptance")
    transfer.fail(error)
    for _ in range(2):
        with pytest.raises(ValueError) as caught:
            await transfer.wait_processing()
        assert caught.value is error
    calls = []
    transfer.add_failure_observer(lambda item, exc: calls.append((item, exc)))
    assert calls == [(transfer, error)] and transfer.failure_observed
    assert transfer.state is TransferState.FAILED
    cancelled = XTransfer("cancelled")
    waiter = asyncio.create_task(cancelled.wait_processing())
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert cancelled.state is TransferState.PENDING
    cancelled.cancel()
