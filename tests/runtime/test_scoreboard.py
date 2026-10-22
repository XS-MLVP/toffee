from __future__ import annotations

import asyncio
import gc
import weakref
from dataclasses import dataclass
from functools import wraps
from contextvars import ContextVar
from typing import Any

import pytest

from toffee import (
    CheckContext,
    Execution,
    MemoryBackend,
    ClockCycles,
    RisingEdge,
    ScoreboardTimeoutError,
    MissingExpectedError,
    Scoreboard,
    ScoreboardAssociationError,
    ScoreboardIncompleteError,
    ScoreboardMismatch,
    ScoreboardMode,
    Transfer,
    TransferState,
    XEvent,
    XEventKind,
    XPhase,
    XTransfer,
)


_simulation = ContextVar("scoreboard_test_simulation")


def _execution():
    return _simulation.get()[0]


def _clock():
    return _simulation.get()[1]


def async_test(function):
    @wraps(function)
    def run():
        async def scenario():
            clock = object()
            async with Execution(MemoryBackend(clock)) as execution:
                token = _simulation.set((execution, clock))
                try:
                    async with asyncio.timeout(2):
                        await function()
                finally:
                    _simulation.reset(token)
        return asyncio.run(scenario())
    return run


def event(tick: int) -> XEvent:
    return XEvent(
        event_id=tick,
        tick=tick,
        phase=XPhase.RISING_STABLE,
        kind=XEventKind.CLOCK_RISE,
    )


@dataclass(frozen=True)
class Request:
    tag: int
    value: int


@dataclass(frozen=True)
class Response:
    tag: int
    value: int


class FakeDriver:
    def __init__(self) -> None:
        self.accepted: asyncio.Queue[XTransfer[Any, Any]] = asyncio.Queue()
        self.outstanding: dict[int, XTransfer[Any, Any]] = {}
        self.quiesced = 0

    def submit(self, request):
        transfer = XTransfer(request)
        self.outstanding[transfer.sequence_id] = transfer
        transfer.add_done_callback(self.retire)
        return transfer

    def accept(self, transfer, tick: int) -> None:
        transfer.mark_processing(event(tick))
        self.accepted.put_nowait(transfer)

    async def recv_accepted(self):
        return await self.accepted.get()

    def retire(self, transfer) -> None:
        self.outstanding.pop(transfer.sequence_id, None)

    def quiesce(self) -> None:
        self.quiesced += 1

    @property
    def has_outstanding(self) -> bool:
        return bool(self.outstanding)


class FakeMonitor:
    def __init__(self) -> None:
        self.observed: asyncio.Queue[Transfer[Any]] = asyncio.Queue()
        self.started = False
        self.starts = 0
        self.closes = 0

    def start(self, execution):
        del execution
        if self.started:
            raise RuntimeError("monitor already started")
        self.started = True
        self.starts += 1
        return self

    async def recv(self):
        return await self.observed.get()

    def observe(self, tick: int, value: Any) -> None:
        self.observed.put_nowait(Transfer(event(tick), value))

    async def aclose(self) -> None:
        if self.started:
            self.started = False
            self.closes += 1


def test_custom_comparator_can_accept_normalized_values():
    comparisons = []

    def compare(expected, actual):
        comparisons.append((expected, actual))
        assert expected.lower() == actual.lower()

    board = Scoreboard("normalized", compare=compare)
    board.check(expected="ack", actual="ACK")
    assert comparisons == [("ack", "ACK")] and board.status.passed == 1


def test_custom_comparator_preserves_an_existing_scoreboard_mismatch():
    original = Scoreboard("source")
    with pytest.raises(ScoreboardMismatch) as caught:
        original.check(expected=1, actual=2)
    failure = caught.value

    def compare(expected, actual):
        raise failure

    board = Scoreboard("forwarded", compare=compare)
    with pytest.raises(ScoreboardMismatch) as forwarded:
        board.check(expected=1, actual=2)
    assert forwarded.value is failure and board.status.failed == 1


@pytest.mark.asyncio
async def test_scoreboard_binding_requires_its_live_execution_and_fresh_instance():
    backend = MemoryBackend(object())
    execution = Execution(backend)
    board = Scoreboard("lifecycle")
    options = dict(driver=FakeDriver(), monitor=FakeMonitor(), clock=backend.clock, response_timeout_cycles=2)
    with pytest.raises(RuntimeError, match="current active Execution"):
        board.bind(execution, **options)
    with pytest.raises(RuntimeError, match="must be bound"):
        board.submit(1)
    with pytest.raises(ValueError, match="observe_cycles"):
        await board.finish(timeout_cycles=1, observe_cycles=-1)
    async with execution:
        board.bind(execution, **options)
        with pytest.raises(RuntimeError, match="already bound"):
            board.bind(execution, **options)
        await board.aclose()
        with pytest.raises(RuntimeError, match="closed"):
            await board.__aenter__()
    assert backend.watcher_count == 0


@async_test
async def test_outstanding_capacity_recovers_after_explicit_cancellation():
    driver, monitor = FakeDriver(), FakeMonitor()
    board = Scoreboard("capacity", capacity=1).bind(
        _execution(), driver=driver, monitor=monitor, clock=_clock(), response_timeout_cycles=5,
    )
    async with board:
        first = board.submit(1, expected=1)
        with pytest.raises(RuntimeError, match="capacity is full"):
            board.submit(2, expected=2)
        first.cancel()
        second = board.submit(2, expected=2)
        second.cancel()
        await board.drain(timeout_cycles=1)
    assert board.status.cancelled == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("processing", [False, True])
async def test_scoreboard_rejects_a_driver_that_returns_an_invalid_submission(processing):
    backend = MemoryBackend(object())

    class BrokenDriver(FakeDriver):
        def submit(self, request):
            if not processing:
                return object()
            transfer = XTransfer(request)
            transfer.mark_processing(event(2))
            transfer.complete(event(2), request)
            return transfer

    async with Execution(backend) as execution:
        board = Scoreboard("driver-contract").bind(
            execution, driver=BrokenDriver(), monitor=FakeMonitor(),
            clock=backend.clock, response_timeout_cycles=2,
        )
        async with board:
            with pytest.raises(TypeError, match="pending XTransfer"):
                board.submit(1, expected=1)
    assert backend.watcher_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("problem,message", [
    ("unknown", "unknown or duplicate"),
    ("duplicate", "unknown or duplicate"),
    ("pending", "not processing"),
    ("event", "rising-stable events"),
    ("key", "duplicate pending association key"),
    ("association", "non-pending transfer"),
])
async def test_malformed_accepted_stream_fails_known_transfers_and_releases_sources(problem, message):
    backend = MemoryBackend(object())
    driver, monitor = FakeDriver(), FakeMonitor()
    async with Execution(backend) as execution:
        options = dict(request_key=lambda value: value, response_key=lambda value: value) if problem == "key" else {}
        board = Scoreboard("accepted-contract").bind(
            execution, driver=driver, monitor=monitor, clock=backend.clock,
            response_timeout_cycles=10, **options,
        )
        async with board:
            first = board.submit(1, expected=1)
            if problem == "unknown":
                driver.accepted.put_nowait(XTransfer(99))
            elif problem == "pending":
                driver.accepted.put_nowait(first)
            else:
                driver.accept(first, -1 if problem == "event" else 2)
                if problem == "duplicate":
                    driver.accepted.put_nowait(first)
                elif problem == "key":
                    second = board.submit(1, expected=1)
                    driver.accept(second, 2)
                elif problem == "association":
                    foreign = XTransfer(99)
                    board.associate = lambda observation, pending: foreign
                    monitor.observe(2, 1)
            with pytest.raises(ScoreboardAssociationError, match=message):
                await board.drain(timeout_cycles=5)
            assert first.state is TransferState.FAILED
    assert driver.outstanding == {} and not monitor.started and backend.watcher_count == 0


@async_test
async def test_finish_rejects_tail_observation_larger_than_its_budget():
    board = Scoreboard("tail-budget").bind(
        _execution(), driver=FakeDriver(), monitor=FakeMonitor(), clock=_clock(), response_timeout_cycles=10,
    )
    async with board:
        with pytest.raises(ScoreboardTimeoutError, match="observation window"):
            await board.finish(timeout_cycles=1, observe_cycles=2)


@async_test
async def test_response_before_acceptance_is_retained_as_unmatched():
    driver, monitor = FakeDriver(), FakeMonitor()
    board = Scoreboard("early-response").bind(
        _execution(), driver=driver, monitor=monitor, clock=_clock(), response_timeout_cycles=10,
    )
    async with board:
        transfer = board.submit(1, expected=1)
        monitor.observe(0, 1)
        driver.accept(transfer, 2)
        monitor.observe(2, 1)
        assert await transfer == 1
        with pytest.raises(ScoreboardAssociationError, match="unmatched observation"):
            await board.finish(timeout_cycles=2)
        assert board.status.passed == 1 and board.status.unmatched_observations == 1


@async_test
async def test_late_response_event_is_rejected_before_clock_checkpoint():
    driver, monitor = FakeDriver(), FakeMonitor()
    board = Scoreboard("late-response").bind(
        _execution(), driver=driver, monitor=monitor, clock=_clock(), response_timeout_cycles=2,
    )
    async with board:
        transfer = board.submit(1, expected=1)
        driver.accept(transfer, 2)
        monitor.observe(8, 1)
        with pytest.raises(ScoreboardTimeoutError):
            await transfer
        assert board.status.passed == 0 and transfer.state is TransferState.FAILED


@pytest.mark.asyncio
async def test_bound_scoreboard_rejects_use_after_execution_exit_and_close():
    backend = MemoryBackend(object())
    async with Execution(backend) as execution:
        board = Scoreboard("lifetime").bind(
            execution, driver=FakeDriver(), monitor=FakeMonitor(), clock=backend.clock, response_timeout_cycles=2,
        )
    async with Execution(MemoryBackend(object())):
        with pytest.raises(RuntimeError, match="bound active Execution"):
            await board.drain(timeout_cycles=1)
    await board.aclose()
    with pytest.raises(RuntimeError, match="closed"):
        await board.drain(timeout_cycles=1)


def test_structural_comparison_detects_different_dataclass_types():
    board = Scoreboard("types")
    with pytest.raises(ScoreboardMismatch) as caught:
        board.check(expected=Request(1, 2), actual=Response(1, 2))
    assert len(caught.value.differences) == 1 and caught.value.differences[0].path == ""


@async_test
async def test_cancelled_accepted_record_is_ignored_and_close_is_idempotent():
    driver, monitor = FakeDriver(), FakeMonitor()
    board = Scoreboard("withdrawn").bind(
        _execution(), driver=driver, monitor=monitor, clock=_clock(), response_timeout_cycles=5,
    )
    async with board:
        transfer = board.submit(1, expected=1)
        driver.accepted.put_nowait(transfer)
        transfer.cancel()
        await ClockCycles(_clock(), 1)
        assert board.status.accepted == 0 and board.status.cancelled == 1
    await board.aclose()
    assert monitor.closes == 1


def test_dataclass_differences_stop_at_the_requested_limit():
    from toffee.scoreboard import structural_differences

    differences = structural_differences(Request(1, 2), Request(3, 4), limit=1)
    assert len(differences) == 1 and differences[0].path == "tag"


def test_direct_check_reports_structured_path_and_status():
    scoreboard = Scoreboard("direct")
    expected = Response(1, 10)
    actual = Response(1, 11)

    with pytest.raises(ScoreboardMismatch) as caught:
        scoreboard.check(expected=expected, actual=actual)

    assert caught.value.context.mode is ScoreboardMode.DIRECT
    assert caught.value.differences[0].path == "value"
    assert "expected 10, actual 11" in str(caught.value)
    assert scoreboard.status.checked == 1
    assert scoreboard.status.failed == 1


def test_custom_compare_assertion_is_preserved_as_cause():
    def compare(expected, actual):
        assert actual.value >= expected.value, "value must not decrease"

    scoreboard = Scoreboard("custom", compare=compare)
    with pytest.raises(ScoreboardMismatch) as caught:
        scoreboard.check(expected=Response(1, 10), actual=Response(1, 9))

    assert isinstance(caught.value.__cause__, AssertionError)
    assert "value must not decrease" in str(caught.value.__cause__)


@async_test
async def test_async_fixed_latency_uses_drain_without_transfer_gather():
    driver = FakeDriver()
    monitor = FakeMonitor()
    scoreboard = Scoreboard("pipeline").bind(
        _execution(), clock=_clock(), response_timeout_cycles=50,
        driver=driver,
        monitor=monitor,
        latency_cycles=2,
    )

    transfers = [
        scoreboard.submit(Request(tag, value), expected=Response(tag, value * 2))
        for tag, value in ((0, 3), (1, 4), (2, 5))
    ]
    for index, transfer in enumerate(transfers):
        driver.accept(transfer, 2 + index * 2)

    # Observations may be queued before accepted-stream tasks run. Association
    # is event based rather than asyncio scheduling based.
    for index, (tag, value) in enumerate(((0, 6), (1, 8), (2, 10))):
        monitor.observe(6 + index * 2, Response(tag, value))

    status = await scoreboard.drain(timeout_cycles=100)
    assert status.submitted == 3
    assert status.checked == 3
    assert status.passed == 3
    assert status.completed == 3
    assert all(item.state is TransferState.COMPLETED for item in transfers)
    assert monitor.closes == 0
    await scoreboard.aclose()


@async_test
async def test_expected_source_runs_at_acceptance():
    driver = FakeDriver()
    monitor = FakeMonitor()
    calls: list[tuple[Request, CheckContext]] = []

    def expected(request, context):
        calls.append((request, context))
        assert context.transfer_state is TransferState.PROCESSING
        return Response(request.tag, request.value * 2)

    scoreboard = Scoreboard("reference", expected=expected).bind(
        _execution(), clock=_clock(), response_timeout_cycles=50, driver=driver, monitor=monitor, latency_cycles=1
    )
    transfer = scoreboard.submit(Request(7, 9))
    assert calls == []

    driver.accept(transfer, 20)
    monitor.observe(22, Response(7, 18))
    await scoreboard.drain(timeout_cycles=100)

    assert calls[0][0] == Request(7, 9)
    assert calls[0][1].accepted_event == event(20)
    await scoreboard.aclose()


class TaggedScoreboard(Scoreboard[Request, Response, Response]):
    def associate(self, observation, pending):
        return next(
            (
                transfer
                for transfer in pending
                if transfer.request.tag == observation.value.tag
            ),
            None,
        )


@async_test
async def test_stateful_reference_uses_acceptance_order_with_cancel_and_reordered_outputs():
    driver, monitor = FakeDriver(), FakeMonitor()
    total = 0
    accepted = []

    def model(request, context):
        nonlocal total
        total += request.value
        accepted.append((request.tag, context.accepted_event.tick))
        return Response(request.tag, total)

    async with Scoreboard("stateful", expected=model).bind(
        _execution(), driver=driver, monitor=monitor, clock=_clock(),
        response_timeout_cycles=10,
        request_key=lambda request: request.tag,
        response_key=lambda response: response.tag,
    ) as board:
        first = board.submit(Request(1, 2))
        second = board.submit(Request(2, 5))
        cancelled = board.submit(Request(3, 100))
        cancelled.cancel()
        assert total == 0
        # Arrival at Python consumers and response order are both independent
        # of the order in which accepted inputs update the reference state.
        monitor.observe(24, Response(1, 7))
        monitor.observe(26, Response(2, 5))
        driver.accept(second, 20)
        driver.accept(first, 22)
        await board.finish(timeout_cycles=50)
        assert await first == Response(1, 7)
        assert await second == Response(2, 5)
        assert total == 7 and accepted == [(2, 20), (1, 22)]


@async_test
async def test_reference_model_failure_is_original_and_not_repeated_on_exit():
    driver, monitor = FakeDriver(), FakeMonitor()
    failure = ValueError("invalid model operation")

    def model(request, context):
        raise failure

    async with Scoreboard("model-failure", expected=model).bind(
        _execution(), driver=driver, monitor=monitor, clock=_clock(),
        response_timeout_cycles=5,
    ) as board:
        transfer = board.submit(Request(1, 2))
        driver.accept(transfer, 2)
        with pytest.raises(ValueError) as caught:
            await transfer
        assert caught.value is failure


def test_expected_source_rejects_async_function():
    async def model(request, context):
        return request

    with pytest.raises(TypeError, match="synchronous"):
        Scoreboard("async-model", expected=model)


@async_test
async def test_expected_source_rejects_awaitable_result_without_leaking_coroutine():
    driver, monitor = FakeDriver(), FakeMonitor()

    async def prediction():
        return Response(1, 2)

    async with Scoreboard("async-result", expected=lambda request, context: prediction()).bind(
        _execution(), driver=driver, monitor=monitor, clock=_clock(),
        response_timeout_cycles=5,
    ) as board:
        transfer = board.submit(Request(1, 2))
        driver.accept(transfer, 2)
        with pytest.raises(TypeError, match="synchronous"):
            await transfer


@async_test
async def test_associate_is_independently_overridable():
    driver = FakeDriver()
    monitor = FakeMonitor()
    scoreboard = TaggedScoreboard("tagged").bind(
        _execution(), clock=_clock(), response_timeout_cycles=50, driver=driver, monitor=monitor
    )
    first = scoreboard.submit(Request(1, 10), expected=Response(1, 20))
    second = scoreboard.submit(Request(2, 11), expected=Response(2, 22))
    driver.accept(first, 2)
    driver.accept(second, 4)
    monitor.observe(6, Response(2, 22))
    monitor.observe(8, Response(1, 20))

    status = await scoreboard.drain(timeout_cycles=100)
    assert status.passed == 2
    assert await first == Response(1, 20)
    assert await second == Response(2, 22)
    await scoreboard.aclose()


@async_test
async def test_fifo_and_key_association_are_built_in():
    fifo_driver = FakeDriver()
    fifo_monitor = FakeMonitor()
    fifo = Scoreboard("fifo").bind(
        _execution(), clock=_clock(), response_timeout_cycles=50, driver=fifo_driver, monitor=fifo_monitor
    )
    fifo_transfers = [
        fifo.submit(Request(tag, tag), expected=Response(tag, tag * 2))
        for tag in (1, 2)
    ]
    for index, transfer in enumerate(fifo_transfers):
        fifo_driver.accept(transfer, 2 + index * 2)
    fifo_monitor.observe(6, Response(1, 2))
    fifo_monitor.observe(8, Response(2, 4))
    assert (await fifo.drain(timeout_cycles=100)).passed == 2
    await fifo.aclose()

    key_driver = FakeDriver()
    key_monitor = FakeMonitor()
    keyed = Scoreboard("keyed").bind(
        _execution(), clock=_clock(), response_timeout_cycles=50,
        driver=key_driver,
        monitor=key_monitor,
        request_key=lambda request: request.tag,
        response_key=lambda response: response.tag,
    )
    first = keyed.submit(Request(1, 1), expected=Response(1, 2))
    second = keyed.submit(Request(2, 2), expected=Response(2, 4))
    key_driver.accept(first, 2)
    key_driver.accept(second, 4)
    key_monitor.observe(6, Response(2, 4))
    key_monitor.observe(8, Response(1, 2))
    assert (await keyed.drain(timeout_cycles=100)).passed == 2
    await keyed.aclose()


@async_test
async def test_duplicate_pending_key_is_an_association_failure():
    driver = FakeDriver()
    monitor = FakeMonitor()
    scoreboard = Scoreboard("duplicate-key").bind(
        _execution(), clock=_clock(), response_timeout_cycles=50,
        driver=driver,
        monitor=monitor,
        request_key=lambda request: request.tag,
        response_key=lambda response: response.tag,
    )
    first = scoreboard.submit(Request(1, 10), expected=Response(1, 20))
    second = scoreboard.submit(Request(1, 11), expected=Response(1, 22))
    driver.accept(first, 2)
    driver.accept(second, 4)
    monitor.observe(6, Response(1, 20))

    with pytest.raises(ScoreboardAssociationError, match="duplicate"):
        await scoreboard.drain(timeout_cycles=100)
    await scoreboard.aclose()


@async_test
async def test_mismatch_reaches_drain_and_is_not_repeated_by_close():
    driver = FakeDriver()
    monitor = FakeMonitor()
    scoreboard = Scoreboard("mismatch").bind(
        _execution(), clock=_clock(), response_timeout_cycles=50, driver=driver, monitor=monitor, latency_cycles=1
    )
    transfer = scoreboard.submit(Request(1, 10), expected=Response(1, 20))
    driver.accept(transfer, 2)
    monitor.observe(4, Response(1, 21))

    with pytest.raises(ScoreboardMismatch, match="sequence_id"):
        await scoreboard.drain(timeout_cycles=100)
    assert transfer.state is TransferState.FAILED
    assert transfer.failure_observed
    await scoreboard.aclose()


@async_test
async def test_mismatch_reaches_awaited_transfer_and_is_not_repeated_by_close():
    driver = FakeDriver()
    monitor = FakeMonitor()
    scoreboard = Scoreboard("awaited-mismatch").bind(
        _execution(), clock=_clock(), response_timeout_cycles=50, driver=driver, monitor=monitor, latency_cycles=1
    )
    transfer = scoreboard.submit(Request(1, 10), expected=Response(1, 20))
    driver.accept(transfer, 2)
    monitor.observe(4, Response(1, 21))

    with pytest.raises(ScoreboardMismatch, match="sequence_id"):
        await transfer
    assert transfer.state is TransferState.FAILED
    assert transfer.failure_observed
    await scoreboard.aclose()


@async_test
async def test_unobserved_background_mismatch_is_reported_by_close():
    driver = FakeDriver()
    monitor = FakeMonitor()
    scoreboard = Scoreboard("close-mismatch").bind(
        _execution(), clock=_clock(), response_timeout_cycles=50, driver=driver, monitor=monitor, latency_cycles=1
    )
    transfer = scoreboard.submit(Request(1, 10), expected=Response(1, 20))
    driver.accept(transfer, 2)
    monitor.observe(4, Response(1, 21))
    while transfer.state is not TransferState.FAILED:
        await asyncio.sleep(0)

    with pytest.raises(ScoreboardMismatch):
        await scoreboard.aclose()


@async_test
async def test_missing_expected_fails_the_processing_transfer():
    driver = FakeDriver()
    monitor = FakeMonitor()
    scoreboard = Scoreboard("missing").bind(
        _execution(), clock=_clock(), response_timeout_cycles=50, driver=driver, monitor=monitor, latency_cycles=1
    )
    transfer = scoreboard.submit(Request(1, 10))
    driver.accept(transfer, 2)

    with pytest.raises(MissingExpectedError):
        await scoreboard.drain(timeout_cycles=100)
    assert transfer.state is TransferState.FAILED
    await scoreboard.aclose()


@async_test
async def test_close_reports_incomplete_transaction():
    driver = FakeDriver()
    monitor = FakeMonitor()
    scoreboard = Scoreboard("incomplete").bind(
        _execution(), clock=_clock(), response_timeout_cycles=50, driver=driver, monitor=monitor, latency_cycles=1
    )
    transfer = scoreboard.submit(Request(1, 10), expected=Response(1, 20))
    driver.accept(transfer, 2)

    with pytest.raises(ScoreboardIncompleteError, match="incomplete"):
        await scoreboard.aclose()
    assert transfer.state is TransferState.FAILED


@async_test
async def test_monitor_stays_started_across_batches():
    driver = FakeDriver()
    monitor = FakeMonitor()
    scoreboard = Scoreboard("batches").bind(
        _execution(), clock=_clock(), response_timeout_cycles=50, driver=driver, monitor=monitor, latency_cycles=1
    )

    first = scoreboard.submit(Request(1, 2), expected=Response(1, 4))
    driver.accept(first, 2)
    monitor.observe(4, Response(1, 4))
    await scoreboard.drain(timeout_cycles=100)

    second = scoreboard.submit(Request(2, 3), expected=Response(2, 6))
    driver.accept(second, 6)
    monitor.observe(8, Response(2, 6))
    status = await scoreboard.drain(timeout_cycles=100)

    assert status.completed == 2
    assert monitor.starts == 1
    assert monitor.closes == 0
    await scoreboard.aclose()


@async_test
async def test_extra_response_is_rejected_at_close():
    driver, monitor = FakeDriver(), FakeMonitor()
    scoreboard = Scoreboard("extra").bind(
        _execution(), clock=_clock(), response_timeout_cycles=50, driver=driver, monitor=monitor,
        request_key=lambda request: request.tag,
        response_key=lambda response: response.tag,
    )
    transfer = scoreboard.submit(Request(1, 10), expected=Response(1, 10))
    driver.accept(transfer, 2)
    monitor.observe(4, Response(99, 99))
    monitor.observe(6, Response(1, 10))
    await scoreboard.drain(timeout_cycles=100)
    with pytest.raises(ScoreboardAssociationError, match="unmatched"):
        await scoreboard.aclose()


@async_test
async def test_processing_transfer_cannot_be_cancelled():
    transfer = XTransfer(Request(1, 10))
    transfer.mark_processing(event(2))
    with pytest.raises(RuntimeError, match="accepted"):
        transfer.cancel()
    assert transfer.state is TransferState.PROCESSING
    transfer.complete(event(4), Response(1, 10))
    assert await transfer == Response(1, 10)


@async_test
async def test_late_fixed_latency_response_fails_without_external_timeout():
    driver, monitor = FakeDriver(), FakeMonitor()
    scoreboard = Scoreboard("late").bind(
        _execution(), clock=_clock(), response_timeout_cycles=50, driver=driver, monitor=monitor, latency_cycles=1,
    )
    transfer = scoreboard.submit(Request(1, 10), expected=Response(1, 10))
    driver.accept(transfer, 2)
    monitor.observe(6, Response(1, 10))
    try:
        # The wall timeout is only a test hang guard, not the expected failure.
        with pytest.raises(ScoreboardTimeoutError, match="deadline") as caught:
            await asyncio.wait_for(scoreboard.drain(timeout_cycles=100), 0.1)
        error = caught.value
        assert error.operation == "fixed_latency"
        assert error.budget_cycles == 1  # The fixed window, not the 50-cycle timeout.
        assert error.start_tick == 2
        assert error.deadline_tick == 4
        assert error.context.request == Request(1, 10)
    finally:
        try:
            await scoreboard.aclose()
        except ScoreboardIncompleteError:
            pass


def bound(name="test", *, driver=None, monitor=None, **options):
    driver = driver or FakeDriver()
    monitor = monitor or FakeMonitor()
    timeout = options.pop("response_timeout_cycles", 10)
    board = Scoreboard(name).bind(
        _execution(), driver=driver, monitor=monitor, clock=_clock(),
        response_timeout_cycles=timeout, **options,
    )
    return board, driver, monitor


@async_test
async def test_missing_response_times_out_when_awaiting_handle():
    board, driver, _ = bound(response_timeout_cycles=2)
    transfer = board.submit(1, expected=2)
    accepted = await RisingEdge(_clock())
    driver.accept(transfer, accepted.tick)
    with pytest.raises(ScoreboardTimeoutError, match="accepted_tick") as caught:
        await transfer
    error = caught.value
    assert error.operation == "response"
    assert error.budget_cycles == 2
    assert error.start_tick == accepted.tick
    assert error.deadline_tick == accepted.tick + 4
    assert error.current_tick == error.deadline_tick + 1
    assert error.elapsed_cycles == 2.5
    assert error.context.sequence_id == transfer.sequence_id
    assert error.status.processing == 1
    assert "budget=2 cycles" in str(error)
    assert "elapsed=2.5 cycles" in str(error)
    assert transfer.state is TransferState.FAILED
    assert board.status.unmatched_requests == 0
    await board.aclose()
    assert _execution().backend.watcher_count == 0


@async_test
async def test_drain_timeout_covers_unaccepted_requests():
    board, _, _ = bound()
    transfer = board.submit(1, expected=2)
    started = _execution().backend.tick
    with pytest.raises(ScoreboardTimeoutError, match="drain deadline") as caught:
        await board.drain(timeout_cycles=2)
    assert caught.value.operation == "drain"
    assert caught.value.start_tick == started
    assert caught.value.budget_cycles == 2
    assert caught.value.context is None
    assert caught.value.status.pending == 1
    assert transfer.state is TransferState.FAILED
    await board.aclose()


@async_test
async def test_pending_cancellation_releases_tracking_and_watermark():
    board, driver, monitor = bound()
    transfer = board.submit(1, expected=2)
    transfer.cancel()
    status = await board.drain(timeout_cycles=2)
    assert status.pending == status.processing == status.unmatched_requests == 0
    assert status.cancelled == 1
    assert not driver.has_outstanding
    await board.finish(timeout_cycles=2)
    with pytest.raises(RuntimeError, match="submissions"):
        board.submit(2, expected=4)
    await board.aclose()
    assert monitor.closes == 1


@async_test
async def test_response_before_acceptance_notification_is_matched():
    board, driver, monitor = bound()
    transfer = board.submit(1, expected=2)
    monitor.observe(4, 2)
    await asyncio.sleep(0)
    driver.accept(transfer, 2)
    assert (await board.finish(timeout_cycles=10)).completed == 1
    await board.aclose()


@pytest.mark.parametrize("observer_first", [False, True])
@pytest.mark.parametrize("delay_delivery", [False, True])
def test_deadline_cycle_response_wins_in_either_registration_order(observer_first, delay_delivery):
    @async_test
    async def scenario():
        board, driver, monitor = bound(response_timeout_cycles=1, latency_cycles=1)
        async def observe():
            await RisingEdge(_clock())
            response = await RisingEdge(_clock())
            if delay_delivery:
                await asyncio.sleep(0)
            monitor.observe(response.tick, 2)
        async def accept():
            acceptance = await RisingEdge(_clock())
            driver.accept(transfer, acceptance.tick)
        # Register both orders before advancing the first clock; otherwise a
        # not-yet-started observer can legitimately miss that first edge.
        async with _execution().paused():
            observer = None
            if observer_first:
                observer = asyncio.create_task(observe())
                await asyncio.sleep(0)
            transfer = board.submit(1, expected=2)
            if observer is None:
                observer = asyncio.create_task(observe())
            acceptance = asyncio.create_task(accept())
            await asyncio.sleep(0)
        await acceptance
        assert await transfer == 2
        await observer
        await board.finish(timeout_cycles=5)
        await board.aclose()
    scenario()


@async_test
async def test_finish_observes_duplicate_after_last_response():
    board, driver, monitor = bound()
    transfer = board.submit(1, expected=2)
    accepted = await RisingEdge(_clock())
    driver.accept(transfer, accepted.tick)
    monitor.observe(accepted.tick, 2)
    await board.drain(timeout_cycles=5)
    async def duplicate():
        sample = await ClockCycles(_clock(), 2)
        monitor.observe(sample.tick, 2)
    task = asyncio.create_task(duplicate())
    with pytest.raises(ScoreboardAssociationError, match="unmatched"):
        await board.finish(timeout_cycles=10, observe_cycles=3)
    await task
    await board.aclose()


@async_test
async def test_sampled_mode_ignores_idle_but_checks_target_cycle():
    board, driver, monitor = bound(latency_cycles=1, sampled=True)
    transfer = board.submit(1, expected=2)
    driver.accept(transfer, 2)
    for tick, value in ((2, -1), (4, 2), (6, -1)):
        monitor.observe(tick, value)
    assert (await board.finish(timeout_cycles=10, observe_cycles=3)).passed == 1
    await board.aclose()


@async_test
async def test_unobserved_failure_propagates_from_context_and_preserves_body_error():
    class BrokenClose(FakeMonitor):
        async def aclose(self):
            await super().aclose()
            raise ValueError("monitor cleanup")
    monitor = BrokenClose()
    board, driver, _ = bound(monitor=monitor)
    with pytest.raises(BaseExceptionGroup) as caught:
        async with board:
            transfer = board.submit(1, expected=2)
            driver.accept(transfer, 2)
            raise LookupError("body failure")
    assert any(isinstance(error, LookupError) for error in caught.value.exceptions)
    assert any(isinstance(error, ValueError) for error in caught.value.exceptions)
    assert transfer.state is TransferState.FAILED
    assert monitor.closes == 1
    assert not board._tasks
    assert _execution().backend.watcher_count == 0


@async_test
async def test_context_closes_on_external_cancellation_without_cancelling_host_task():
    host_gate = asyncio.Event()
    host = asyncio.create_task(host_gate.wait())
    board, driver, monitor = bound()
    started = asyncio.Event()
    async def child():
        async with board:
            transfer = board.submit(1, expected=2)
            driver.accept(transfer, 2)
            started.set()
            await asyncio.Event().wait()
    task = asyncio.create_task(child())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert monitor.closes == 1
    assert not board._tasks
    assert _execution().backend.watcher_count == 0
    assert not host.done()
    host_gate.set()
    await host


@async_test
async def test_completed_requests_are_not_retained_and_stats_are_cumulative():
    board, driver, monitor = bound(response_timeout_cycles=20)
    refs = []
    for index in range(200):
        request = Request(index, index)
        refs.append(weakref.ref(request))
        transfer = board.submit(request, expected=index)
        sample = await RisingEdge(_clock())
        driver.accept(transfer, sample.tick)
        monitor.observe(sample.tick, index)
        await board.drain(timeout_cycles=10)
    del request, transfer
    await asyncio.sleep(0)
    gc.collect()
    assert board.status.completed == 200
    assert not board._transfers and not board._pending
    assert all(ref() is None for ref in refs)
    await board.aclose()


def test_default_mismatch_computes_differences_once(monkeypatch):
    import toffee.scoreboard as module
    original = module.structural_differences
    calls = []
    def compare(*args, **kwargs):
        calls.append(None)
        return original(*args, **kwargs)
    monkeypatch.setattr(module, "structural_differences", compare)
    with pytest.raises(ScoreboardMismatch):
        Scoreboard("once").check(expected={"a": 1}, actual={"a": 2})
    assert len(calls) == 1
    assert len(original({}, {str(i): i for i in range(50)}, limit=2)) == 2


@async_test
async def test_drain_watermark_does_not_wait_for_later_submission():
    board, driver, monitor = bound()
    first = board.submit(1, expected=2)
    waiter = asyncio.create_task(board.drain(timeout_cycles=10))
    await asyncio.sleep(0)
    second = board.submit(3, expected=6)
    driver.accept(first, 2)
    monitor.observe(4, 2)
    status = await waiter
    assert status.completed == 1 and status.pending == 1
    second.cancel()
    await board.finish(timeout_cycles=5)
    await board.aclose()


@async_test
async def test_partial_monitor_start_is_cleaned_up():
    class PartialStart(FakeMonitor):
        def start(self, execution):
            super().start(execution)
            raise ValueError("monitor start failure")
    monitor = PartialStart()
    board, driver, _ = bound(monitor=monitor)
    with pytest.raises(ValueError, match="start failure"):
        board.submit(1, expected=2)
    await board.aclose()
    assert monitor.closes == 1
    assert not driver.has_outstanding
    assert _execution().backend.watcher_count == 0


@async_test
async def test_monitor_exception_without_a_waited_handle_reaches_close():
    class BrokenSource(FakeMonitor):
        async def recv(self):
            raise ValueError("observation source failure")
    board, _, monitor = bound(monitor=BrokenSource())
    with pytest.raises(ValueError, match="observation source failure"):
        async with board:
            await asyncio.sleep(0)
    assert monitor.closes == 1 and not board._tasks


@async_test
async def test_extra_observations_are_bounded():
    driver, monitor = FakeDriver(), FakeMonitor()
    board = Scoreboard("bounded", capacity=2).bind(
        _execution(), driver=driver, monitor=monitor, clock=_clock(),
        response_timeout_cycles=10,
    )
    with pytest.raises(ScoreboardAssociationError, match="capacity"):
        async with board:
            for i in range(3):
                monitor.observe(2, i)
            await asyncio.sleep(0)
    assert not board._tasks


@async_test
async def test_sampled_idle_does_not_accumulate_while_input_is_pending():
    board, _, monitor = bound(latency_cycles=1, sampled=True)
    transfer = board.submit(1, expected=2)
    for _ in range(80):
        sample = await RisingEdge(_clock())
        monitor.observe(sample.tick, -1)
    transfer.cancel()
    await board.finish(timeout_cycles=5)
    await board.aclose()


@async_test
async def test_finish_total_budget_includes_observation_window():
    board, _, _ = bound()
    with pytest.raises(ScoreboardTimeoutError, match="observation window") as caught:
        await board.finish(timeout_cycles=1, observe_cycles=2)
    assert caught.value.operation == "finish"
    assert caught.value.budget_cycles == 1
    assert caught.value.elapsed_cycles == 0
    assert caught.value.current_tick < caught.value.deadline_tick
    await board.aclose()


@async_test
async def test_finish_timeout_identifies_total_budget_while_draining():
    board, _, _ = bound()
    board.submit(1, expected=2)
    with pytest.raises(ScoreboardTimeoutError, match="finish deadline") as caught:
        await board.finish(timeout_cycles=2)
    assert caught.value.operation == "finish"
    assert caught.value.budget_cycles == 2
    assert caught.value.status.pending == 1
    await board.aclose()
