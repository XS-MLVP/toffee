from __future__ import annotations

import asyncio
from functools import wraps

import pytest

from toffee import TransferState, XEvent, XEventKind, XPhase, XTransfer


def async_test(function):
    @wraps(function)
    def run():
        return asyncio.run(function())

    return run


def event(tick: int) -> XEvent:
    return XEvent(
        event_id=tick,
        tick=tick,
        phase=XPhase.RISING_STABLE,
        kind=XEventKind.CLOCK_RISE,
    )


@async_test
async def test_transfer_lifecycle_and_terminal_callback():
    transfer: XTransfer[str, int] = XTransfer("request")
    processing = asyncio.create_task(transfer.wait_processing())
    completed = []
    transfer.add_done_callback(completed.append)

    transfer.mark_processing(event(2))
    assert await processing == event(2)
    assert transfer.state is TransferState.PROCESSING

    transfer.complete(event(6), 42)
    assert await transfer == 42
    assert transfer.state is TransferState.COMPLETED
    assert transfer.accepted_event == event(2)
    assert transfer.completed_event == event(6)
    assert completed == [transfer]


@async_test
async def test_transfer_failure_is_acknowledged_when_awaited():
    transfer: XTransfer[str, int] = XTransfer("request")
    observed = []
    transfer.add_failure_observer(lambda item, error: observed.append((item, error)))
    error = ValueError("broken")

    transfer.mark_processing(event(2))
    transfer.fail(error)

    with pytest.raises(ValueError, match="broken"):
        await transfer
    assert transfer.failure_observed
    assert observed == [(transfer, error)]


@async_test
async def test_transfer_rejects_invalid_transitions_and_expected_overwrite():
    transfer: XTransfer[str, int] = XTransfer("request")
    transfer.set_expected(1)
    with pytest.raises(RuntimeError, match="already has"):
        transfer.set_expected(2)
    with pytest.raises(RuntimeError, match="cannot complete"):
        transfer.complete(event(2), 42)

    transfer.cancel()
    assert transfer.state is TransferState.CANCELLED
    with pytest.raises(asyncio.CancelledError):
        await transfer


@async_test
async def test_cancelling_waiter_does_not_cancel_or_observe_transfer_failure():
    transfer = XTransfer("request")
    waiter = asyncio.ensure_future(transfer)
    await asyncio.sleep(0)
    waiter.cancel()
    transfer.fail(ValueError("failure after cancellation"))
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert not transfer.failure_observed
    with pytest.raises(ValueError):
        await transfer
    assert transfer.failure_observed
